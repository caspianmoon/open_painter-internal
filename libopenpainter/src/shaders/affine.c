#include "ir_rules.h"
#include <stdlib.h>
#include <string.h>

/* Blend modes whose opaque-backdrop form base + (value(base, s) - base) * alpha
 * is affine in base with coefficients fixed by the source. HARD_LIGHT branches
 * on the source only. Every other mode clamps, branches on the backdrop or
 * normalizes, and ends an affine run. */
static const char *const affine_modes[] = {"NORMAL",   "DISABLE",   "MULTIPLY", "ADD",
                                           "SUBTRACT", "EXCLUSION", "SCREEN",   "HARD_LIGHT",
                                           NULL};

static int affine_mode(const char *name) {
    if (!memchr(name, 0, 64))
        return 0;
    for (unsigned i = 0; affine_modes[i]; i++)
        if (!strcmp(affine_modes[i], name))
            return 1;
    return 0;
}

static unsigned width(uint32_t type) { return type == OP_IR_FLOAT ? 1u : type == OP_IR_VECTOR ? 3u : 4u; }

static int single(uint8_t mask) { return !mask || !(mask & (uint8_t)(mask - 1u)); }

static uint8_t lowest(uint8_t mask) {
    for (uint8_t c = 0; c < 4; c++)
        if (mask & (1u << c))
            return c;
    return OP_IR_NO_COMPONENT;
}

/* Forward dependency analysis. dep[i][c] is the set of backdrop components that
 * component c of node i depends on, always through linear operations; bad[i]
 * marks a node whose value depends on the backdrop in any other way. */
op_status_t op_ir_backdrop_affine(const op_ir_node_t *nodes, uint32_t count,
                                  const op_ir_target_t *targets, uint32_t target_count,
                                  uint32_t backdrop, uint32_t *affine, uint8_t source[4]) {
    if (!affine || !source)
        return OP_INVALID_ARGUMENT;
    *affine = 0;
    memset(source, OP_IR_NO_COMPONENT, 4);
    if (!nodes || !count || count > 262144 || !targets || !target_count || target_count > 8)
        return OP_INVALID_ARGUMENT;
    uint8_t(*dep)[4] = calloc(count, sizeof(*dep));
    unsigned char *bad = calloc(count, 1);
    if (!dep || !bad) {
        free(dep);
        free(bad);
        return OP_RESOURCE_EXHAUSTED;
    }
    op_status_t status = OP_OK;
    for (uint32_t i = 0; i < count; i++) {
        const op_ir_node_t *n = &nodes[i];
        unsigned inputs = op_ir_arity(n->opcode);
        int tainted = 0;
        if (n->type < OP_IR_FLOAT || n->type > OP_IR_COLOR) {
            status = OP_VALIDATION_FAILED;
            goto done;
        }
        for (unsigned j = 0; j < inputs; j++) {
            uint32_t in = n->inputs[j];
            if (in >= i) {
                status = OP_VALIDATION_FAILED;
                goto done;
            }
            tainted |= bad[in] || dep[in][0] || dep[in][1] || dep[in][2] || dep[in][3];
        }
        switch (n->opcode) {
        case OP_IR_FETCH:
            if (n->aux[0] == backdrop)
                for (unsigned c = 0; c < 4; c++)
                    dep[i][c] = (uint8_t)(1u << c);
            break;
        case OP_IR_CONVERT: {
            const uint8_t *in = dep[n->inputs[0]];
            uint32_t from = nodes[n->inputs[0]].type;
            bad[i] = bad[n->inputs[0]];
            if (from == n->type)
                memcpy(dep[i], in, 4);
            else if (n->type == OP_IR_FLOAT) /* average or luminance: linear, mixing */
                dep[i][0] = in[0] | in[1] | in[2];
            else if (from == OP_IR_FLOAT) /* replicate; a color gains a constant alpha */
                dep[i][0] = dep[i][1] = dep[i][2] = in[0];
            else /* vector <-> color keeps rgb; a color's alpha becomes 1 */
                memcpy(dep[i], in, 3);
            break;
        }
        case OP_IR_COMPONENT:
            if (n->aux[0] >= width(nodes[n->inputs[0]].type)) {
                status = OP_VALIDATION_FAILED;
                goto done;
            }
            bad[i] = bad[n->inputs[0]];
            dep[i][0] = dep[n->inputs[0]][n->aux[0]];
            break;
        case OP_IR_COMBINE:
            for (unsigned c = 0; c < 3; c++) {
                bad[i] |= bad[n->inputs[c]];
                dep[i][c] = dep[n->inputs[c]][0];
            }
            break;
        case OP_IR_BLEND: {
            const uint8_t *base = dep[n->inputs[0]];
            int others = 0;
            for (unsigned j = 1; j < 5; j++) {
                const uint8_t *d = dep[n->inputs[j]];
                others |= bad[n->inputs[j]] || d[0] || d[1] || d[2] || d[3];
            }
            if (bad[n->inputs[0]] || others) {
                bad[i] = 1;
                break;
            }
            if (base[0] | base[1] | base[2]) {
                /* Only the opaque specialization ignores the backdrop alpha;
                 * the general form divides by it. */
                if (n->aux[0] != 1 || !affine_mode(n->operation)) {
                    bad[i] = 1;
                    break;
                }
                memcpy(dep[i], base, 3); /* separable per component; alpha is 1 */
            }
            break;
        }
        default:
            bad[i] = (unsigned char)tainted;
            break;
        }
    }
    if (target_count == 1) {
        const op_ir_target_t *t = targets;
        uint8_t out[4] = {0};
        if (t->color >= count || t->alpha >= count || t->layout > 2) {
            status = OP_VALIDATION_FAILED;
            goto done;
        }
        if (bad[t->color] || bad[t->alpha])
            goto done;
        if (t->layout == 2) {
            out[0] = out[1] = out[2] = dep[t->color][0];
        } else if (t->layout == 1) {
            out[0] = dep[t->color][0];
            out[1] = dep[t->alpha][0];
        } else {
            memcpy(out, dep[t->color], 3);
            out[3] = dep[t->alpha][0];
        }
        for (unsigned c = 0; c < 4; c++)
            if (!single(out[c]))
                goto done; /* cross-component mixing: not a per-component map */
        for (unsigned c = 0; c < 4; c++)
            source[c] = lowest(out[c]);
        *affine = 1;
    }
done:
    free(dep);
    free(bad);
    return status;
}
