#include "openpainter/op_ir.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* CPU port of blend.inc's separable modes (opaque form), for checking which
 * modes are affine in the backdrop independently of the classifier. */
static double divide(double a, double b) { return fabs(b) > 1e-8 ? a / b : 1.0; }
static double clampd(double v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
static double burn(double a, double s) {
    if (a >= 1.0)
        return 1.0;
    return s > 0.0 ? 1.0 - fmin(1.0, divide(1.0 - a, s)) : 0.0;
}
static double dodge(double a, double s) {
    if (a <= 0.0)
        return 0.0;
    return s < 1.0 ? fmin(1.0, divide(a, 1.0 - s)) : 1.0;
}
static double component(double a, double s, int mode) {
    switch (mode) {
    case 0: return s;
    case 1: return a;
    case 2: return a * s;
    case 3: return a + s;
    case 4: return a - s;
    case 5: return fmin(a, s);
    case 6: return fmax(a, s);
    case 7: return clampd(divide(a, s));
    case 8: return fabs(a - s);
    case 9: return a + s - 2.0 * (a * s);
    case 10: return 1.0 - (1.0 - a) * (1.0 - s);
    case 11: return fmax(0.0, a + s - 1.0);
    case 12: return burn(a, s);
    case 13: return dodge(a, s);
    case 14:
    case 15: {
        double lower = 2.0 * (a * s), upper = 1.0 - 2.0 * ((1.0 - a) * (1.0 - s));
        return (mode == 14 ? a : s) > 0.5 ? upper : lower;
    }
    case 16: {
        double curve = a > 0.25 ? sqrt(fmax(a, 0.0)) : ((16.0 * a - 12.0) * a + 4.0) * a;
        return s > 0.5 ? a + (2.0 * s - 1.0) * (curve - a) : a - (1.0 - 2.0 * s) * (a * (1.0 - a));
    }
    case 17: return s > 0.5 ? dodge(a, 2.0 * s - 1.0) : burn(a, 2.0 * s);
    case 18: return clampd(a + 2.0 * s - 1.0);
    case 19: return s > 0.5 ? fmax(a, 2.0 * s - 1.0) : fmin(a, 2.0 * s);
    default: return s;
    }
}
static double opaque_blend(double base, double s, double alpha, int mode) {
    if (mode == 1)
        return base;
    return base + (component(base, s, mode) - base) * alpha;
}
static const char *modes[] = {"NORMAL",      "DISABLE",     "MULTIPLY",   "ADD",
                              "SUBTRACT",    "DARKEN",      "LIGHTEN",    "DIVIDE",
                              "DIFFERENCE",  "EXCLUSION",   "SCREEN",     "LINEAR_BURN",
                              "COLOR_BURN",  "COLOR_DODGE", "OVERLAY",    "HARD_LIGHT",
                              "SOFT_LIGHT",  "VIVID_LIGHT", "LINEAR_LIGHT", "PIN_LIGHT",
                              "HUE",         "SATURATION",  "COLOR",      "LUMINOSITY",
                              "NORMAL_COMBINE", "RNM",      "NORMAL_ADD", "NORMAL_REPLACE"};

static uint32_t add(op_ir_node_t *n, uint32_t *count, uint32_t opcode, uint32_t type) {
    memset(&n[*count], 0, sizeof(n[*count]));
    n[*count].opcode = opcode;
    n[*count].type = type;
    return (*count)++;
}

/* backdrop fetch -> rgb -> opaque blend -> color + alpha target (layout 0). */
static uint32_t channel_unit(op_ir_node_t *n, const char *mode, uint32_t opaque,
                             op_ir_target_t *target) {
    uint32_t c = 0;
    uint32_t fetch = add(n, &c, OP_IR_FETCH, OP_IR_COLOR);
    n[fetch].aux[0] = 3;
    n[fetch].aux[1] = 1;
    uint32_t base = add(n, &c, OP_IR_CONVERT, OP_IR_VECTOR);
    n[base].inputs[0] = fetch;
    uint32_t one = add(n, &c, OP_IR_CONSTANT, OP_IR_FLOAT);
    n[one].value[0] = 1.0;
    uint32_t source = add(n, &c, OP_IR_CONSTANT, OP_IR_VECTOR);
    n[source].value[0] = .2;
    n[source].value[1] = .4;
    n[source].value[2] = .8;
    uint32_t coverage = add(n, &c, OP_IR_PARAMETER, OP_IR_FLOAT);
    uint32_t blend = add(n, &c, OP_IR_BLEND, OP_IR_COLOR);
    n[blend].inputs[0] = base;
    n[blend].inputs[1] = one;
    n[blend].inputs[2] = source;
    n[blend].inputs[3] = one;
    n[blend].inputs[4] = coverage;
    n[blend].aux[0] = opaque;
    snprintf(n[blend].operation, sizeof n[blend].operation, "%s", mode);
    uint32_t color = add(n, &c, OP_IR_CONVERT, OP_IR_VECTOR);
    n[color].inputs[0] = blend;
    uint32_t alpha = add(n, &c, OP_IR_COMPONENT, OP_IR_FLOAT);
    n[alpha].inputs[0] = blend;
    n[alpha].aux[0] = 3;
    target->color = color;
    target->alpha = alpha;
    target->layout = 0;
    return c;
}

static uint32_t classify(const op_ir_node_t *n, uint32_t count, const op_ir_target_t *t,
                         uint32_t targets, uint8_t source[4]) {
    uint32_t affine = 7;
    assert(op_ir_backdrop_affine(n, count, t, targets, 3, &affine, source) == OP_OK);
    assert(affine <= 1);
    return affine;
}

int main(void) {
    op_ir_node_t n[32];
    op_ir_target_t t;
    uint8_t source[4];
    int whitelisted[28] = {0};

    /* The classifier's affine modes are exactly affine; every excluded
     * separable mode demonstrably is not. */
    srand(7);
    for (unsigned m = 0; m < 28; m++) {
        uint32_t count = channel_unit(n, modes[m], 1, &t);
        whitelisted[m] = (int)classify(n, count, &t, 1, source);
        if (whitelisted[m])
            assert(source[0] == 0 && source[1] == 1 && source[2] == 2 &&
                   source[3] == OP_IR_NO_COMPONENT);
        if (m >= 20)
            continue; /* hue/saturation/color/luminosity and normals mix components */
        double worst = 0.0;
        for (unsigned trial = 0; trial < 4000; trial++) {
            double s = rand() / (double)RAND_MAX * 1.2 - .1, alpha = rand() / (double)RAND_MAX;
            double x = rand() / (double)RAND_MAX * 1.4 - .2;
            double f0 = opaque_blend(0.0, s, alpha, (int)m), f1 = opaque_blend(1.0, s, alpha, (int)m);
            double error = fabs(opaque_blend(x, s, alpha, (int)m) - (f0 + (f1 - f0) * x));
            worst = error > worst ? error : worst;
        }
        if (whitelisted[m])
            assert(worst < 1e-12);
        else
            assert(worst > 1e-3);
    }
    for (unsigned m = 20; m < 28; m++)
        assert(!whitelisted[m]);

    /* The general (non-opaque) blend divides by the backdrop alpha. */
    uint32_t count = channel_unit(n, "NORMAL", 0, &t);
    assert(!classify(n, count, &t, 1, source));

    /* Any other operation on the backdrop ends an affine run: here the
     * coverage is the squared backdrop red. */
    count = 0;
    uint32_t fetch = add(n, &count, OP_IR_FETCH, OP_IR_COLOR);
    n[fetch].aux[0] = 3;
    n[fetch].aux[1] = 1;
    uint32_t base = add(n, &count, OP_IR_CONVERT, OP_IR_VECTOR);
    n[base].inputs[0] = fetch;
    uint32_t red = add(n, &count, OP_IR_COMPONENT, OP_IR_FLOAT);
    n[red].inputs[0] = fetch;
    uint32_t square = add(n, &count, OP_IR_MATH, OP_IR_FLOAT);
    n[square].inputs[0] = n[square].inputs[1] = n[square].inputs[2] = red;
    snprintf(n[square].operation, sizeof n[square].operation, "%s", "MULTIPLY");
    uint32_t one = add(n, &count, OP_IR_CONSTANT, OP_IR_FLOAT);
    n[one].value[0] = 1.0;
    uint32_t grey = add(n, &count, OP_IR_CONSTANT, OP_IR_VECTOR);
    n[grey].value[0] = n[grey].value[1] = n[grey].value[2] = .3;
    uint32_t blend = add(n, &count, OP_IR_BLEND, OP_IR_COLOR);
    n[blend].inputs[0] = base;
    n[blend].inputs[1] = n[blend].inputs[3] = one;
    n[blend].inputs[2] = grey;
    n[blend].inputs[4] = square;
    n[blend].aux[0] = 1;
    snprintf(n[blend].operation, sizeof n[blend].operation, "%s", "NORMAL");
    uint32_t color = add(n, &count, OP_IR_CONVERT, OP_IR_VECTOR);
    n[color].inputs[0] = blend;
    uint32_t alpha = add(n, &count, OP_IR_COMPONENT, OP_IR_FLOAT);
    n[alpha].inputs[0] = blend;
    n[alpha].aux[0] = 3;
    t = (op_ir_target_t){color, alpha, 0};
    assert(!classify(n, count, &t, 1, source));
    /* The same unit with a backdrop-independent coverage is affine. */
    n[blend].inputs[4] = one;
    assert(classify(n, count, &t, 1, source));

    /* A backdrop-derived image coordinate is not affine either. */
    count = 0;
    fetch = add(n, &count, OP_IR_FETCH, OP_IR_COLOR);
    n[fetch].aux[0] = 3;
    n[fetch].aux[1] = 1;
    base = add(n, &count, OP_IR_CONVERT, OP_IR_VECTOR);
    n[base].inputs[0] = fetch;
    uint32_t image = add(n, &count, OP_IR_IMAGE, OP_IR_COLOR);
    n[image].inputs[0] = base;
    uint32_t rgb = add(n, &count, OP_IR_CONVERT, OP_IR_VECTOR);
    n[rgb].inputs[0] = image;
    one = add(n, &count, OP_IR_CONSTANT, OP_IR_FLOAT);
    n[one].value[0] = 1.0;
    blend = add(n, &count, OP_IR_BLEND, OP_IR_COLOR);
    n[blend].inputs[0] = base;
    n[blend].inputs[1] = n[blend].inputs[3] = n[blend].inputs[4] = one;
    n[blend].inputs[2] = rgb;
    n[blend].aux[0] = 1;
    snprintf(n[blend].operation, sizeof n[blend].operation, "%s", "NORMAL");
    color = add(n, &count, OP_IR_CONVERT, OP_IR_VECTOR);
    n[color].inputs[0] = blend;
    alpha = add(n, &count, OP_IR_COMPONENT, OP_IR_FLOAT);
    n[alpha].inputs[0] = blend;
    n[alpha].aux[0] = 3;
    t = (op_ir_target_t){color, alpha, 0};
    assert(!classify(n, count, &t, 1, source));

    /* A bypassed unit passes its backdrop through: identity per component. */
    count = 0;
    fetch = add(n, &count, OP_IR_FETCH, OP_IR_COLOR);
    n[fetch].aux[0] = 3;
    n[fetch].aux[1] = 1;
    color = add(n, &count, OP_IR_CONVERT, OP_IR_VECTOR);
    n[color].inputs[0] = fetch;
    alpha = add(n, &count, OP_IR_COMPONENT, OP_IR_FLOAT);
    n[alpha].inputs[0] = fetch;
    n[alpha].aux[0] = 3;
    t = (op_ir_target_t){color, alpha, 0};
    assert(classify(n, count, &t, 1, source));
    assert(source[0] == 0 && source[1] == 1 && source[2] == 2 && source[3] == 3);
    /* A different sampler is not the backdrop: the output is constant in it. */
    n[fetch].aux[0] = 4;
    assert(classify(n, count, &t, 1, source));
    for (unsigned c = 0; c < 4; c++)
        assert(source[c] == OP_IR_NO_COMPONENT);
    n[fetch].aux[0] = 3;
    /* Two targets cannot be materialized from one affine map. */
    op_ir_target_t two[2] = {t, t};
    assert(!classify(n, count, two, 2, source));

    /* Packed scalar units: value in r, coverage in g. */
    count = 0;
    fetch = add(n, &count, OP_IR_FETCH, OP_IR_COLOR);
    n[fetch].aux[0] = 3;
    n[fetch].aux[1] = 1;
    grey = add(n, &count, OP_IR_CONSTANT, OP_IR_VECTOR);
    n[grey].value[0] = n[grey].value[1] = n[grey].value[2] = .3;
    one = add(n, &count, OP_IR_CONSTANT, OP_IR_FLOAT);
    n[one].value[0] = 1.0;
    uint32_t value = add(n, &count, OP_IR_COMPONENT, OP_IR_FLOAT);
    n[value].inputs[0] = fetch;
    base = add(n, &count, OP_IR_CONVERT, OP_IR_VECTOR);
    n[base].inputs[0] = value;
    blend = add(n, &count, OP_IR_BLEND, OP_IR_COLOR);
    n[blend].inputs[0] = base;
    n[blend].inputs[1] = n[blend].inputs[3] = n[blend].inputs[4] = one;
    n[blend].inputs[2] = base; /* the backdrop also feeds the source: rejected */
    n[blend].aux[0] = 1;
    snprintf(n[blend].operation, sizeof n[blend].operation, "%s", "MULTIPLY");
    color = add(n, &count, OP_IR_CONVERT, OP_IR_VECTOR);
    n[color].inputs[0] = blend;
    alpha = add(n, &count, OP_IR_COMPONENT, OP_IR_FLOAT);
    n[alpha].inputs[0] = blend;
    n[alpha].aux[0] = 3;
    t = (op_ir_target_t){color, alpha, 1};
    assert(!classify(n, count, &t, 1, source));
    n[blend].inputs[2] = grey;
    assert(classify(n, count, &t, 1, source));
    assert(source[0] == 0 && source[1] == OP_IR_NO_COMPONENT && source[2] == OP_IR_NO_COMPONENT &&
           source[3] == OP_IR_NO_COMPONENT);
    /* Replicated scalar layout: every color component reads backdrop red. */
    uint32_t scalar = add(n, &count, OP_IR_COMPONENT, OP_IR_FLOAT);
    n[scalar].inputs[0] = blend;
    t = (op_ir_target_t){scalar, alpha, 2};
    assert(classify(n, count, &t, 1, source));
    assert(source[0] == 0 && source[1] == 0 && source[2] == 0 &&
           source[3] == OP_IR_NO_COMPONENT);
    /* Luminance mixes backdrop components: not representable per component. */
    count = 0;
    fetch = add(n, &count, OP_IR_FETCH, OP_IR_COLOR);
    n[fetch].aux[0] = 3;
    n[fetch].aux[1] = 1;
    uint32_t luminance = add(n, &count, OP_IR_CONVERT, OP_IR_FLOAT);
    n[luminance].inputs[0] = fetch;
    one = add(n, &count, OP_IR_CONSTANT, OP_IR_FLOAT);
    n[one].value[0] = 1.0;
    t = (op_ir_target_t){luminance, one, 2};
    assert(!classify(n, count, &t, 1, source));

    /* Malformed input is an error, not a classification. */
    uint32_t affine = 0;
    assert(op_ir_backdrop_affine(NULL, 1, &t, 1, 3, &affine, source) == OP_INVALID_ARGUMENT);
    assert(op_ir_backdrop_affine(n, count, &t, 1, 3, NULL, source) == OP_INVALID_ARGUMENT);
    n[luminance].inputs[0] = luminance;
    assert(op_ir_backdrop_affine(n, count, &t, 1, 3, &affine, source) == OP_VALIDATION_FAILED);
    n[luminance].inputs[0] = fetch;
    t.color = 99;
    assert(op_ir_backdrop_affine(n, count, &t, 1, 3, &affine, source) == OP_VALIDATION_FAILED);
    puts("PASS test_affine");
    return 0;
}
