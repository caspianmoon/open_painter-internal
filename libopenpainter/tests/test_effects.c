#include "openpainter/op_ir.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
int main(void) {
    const struct {
        const char *name;
        unsigned count, vectors;
    } specs[] = {{"LEVELS", 5, 0},
                 {"ROUGHNESS_REMAP", 5, 0},
                 {"METALLIC_REMAP", 5, 0},
                 {"HEIGHT_REMAP", 5, 0},
                 {"GAMMA", 1, 0},
                 {"INVERT", 0, 0},
                 {"CLAMP", 2, 0},
                 {"BRIGHTNESS_CONTRAST", 2, 0},
                 {"ROUGHNESS_CONTRAST", 1, 0},
                 {"THRESHOLD", 1, 0},
                 {"HISTOGRAM_SCAN", 3, 0},
                 {"HISTOGRAM_RANGE", 4, 0},
                 {"POSTERIZE", 1, 0},
                 {"QUANTIZE", 1, 0},
                 {"AO_STRENGTH", 1, 0},
                 {"NORMAL_STRENGTH", 1, 0},
                 {"NORMAL_INVERT_Y", 0, 0},
                 {"NORMAL_COMBINE", 2, 1},
                 {"HSL", 3, 0},
                 {"HSV", 3, 0},
                 {"HUE_SHIFT", 3, 0},
                 {"SATURATION", 3, 0},
                 {"COLOR_BALANCE", 1, 1},
                 {"GRADIENT_MAP", 2, 3},
                 {"COLORIZE", 2, 1},
                 {"TINT", 2, 1},
                 {"TEMPERATURE", 1, 0},
                 {"DESATURATE", 0, 0},
                 {"REPLACE_COLOR", 3, 3},
                 {"CHANNEL_MIXER", 4, 15}};
    op_ir_node_t nodes[9] = {0};
    op_ir_target_t target = {8, 7, 0};
    char *source = NULL;
    for (unsigned k = 0; k < 30; k++) {
        memset(nodes, 0, sizeof(nodes));
        for (unsigned j = 0; j < 8; j++) {
            nodes[j].opcode = OP_IR_CONSTANT;
            nodes[j].type =
                (j == 0 || (j <= specs[k].count && (specs[k].vectors & (1u << (j - 1)))))
                    ? OP_IR_VECTOR
                    : OP_IR_FLOAT;
            nodes[j].value[0] = .5;
            nodes[8].inputs[j] = j;
        }
        nodes[8].opcode = OP_IR_EFFECT;
        nodes[8].type = OP_IR_VECTOR;
        snprintf(nodes[8].operation, sizeof nodes[8].operation, "%s", specs[k].name);
        assert(op_ir_compile(nodes, 9, &target, 1, &source) == OP_OK);
        assert(strstr(source, "vec3 v8 = op_effect(v0"));
        op_ir_free(source);
        for (unsigned j = 0; j < 8; j++) {
            nodes[8].inputs[j] = 8;
            assert(op_ir_compile(nodes, 9, &target, 1, &source) == OP_VALIDATION_FAILED && !source);
            nodes[8].inputs[j] = j;
            if (j < specs[k].count + 3) {
                unsigned old = nodes[j].type;
                nodes[j].type = old == OP_IR_FLOAT ? OP_IR_VECTOR : OP_IR_FLOAT;
                assert(op_ir_compile(nodes, 9, &target, 1, &source) == OP_VALIDATION_FAILED &&
                       !source);
                nodes[j].type = old;
            }
        }
    }
    snprintf(nodes[8].operation, sizeof nodes[8].operation, "%s", "inject();");
    assert(op_ir_compile(nodes, 9, &target, 1, &source) == OP_UNSUPPORTED && !source);
    memset(nodes[8].operation, 'x', 64);
    assert(op_ir_compile(nodes, 9, &target, 1, &source) == OP_VALIDATION_FAILED && !source);
    memset(nodes, 0, sizeof(nodes));
    for (unsigned j = 0; j < 8; j++) {
        nodes[j].opcode = OP_IR_CONSTANT;
        nodes[j].type = OP_IR_FLOAT;
        nodes[8].inputs[j] = j;
    }
    nodes[8].opcode = OP_IR_MASK_FOLD;
    nodes[8].type = OP_IR_FLOAT;
    target.layout = 2;
    const char *modes[] = {"NORMAL", "MULTIPLY", "MAX", "MIN", "ADD"};
    for (unsigned k = 0; k < 5; k++)
        for (unsigned first = 0; first < 2; first++) {
            snprintf(nodes[8].operation, sizeof nodes[8].operation, "%s", modes[k]);
            nodes[8].aux[0] = first;
            assert(op_ir_compile(nodes, 9, &target, 1, &source) == OP_OK);
            assert(strstr(source, "float v8 = op_mask_fold("));
            op_ir_free(source);
        }
    nodes[8].aux[0] = 2;
    assert(op_ir_compile(nodes, 9, &target, 1, &source) == OP_VALIDATION_FAILED && !source);
    nodes[8].aux[0] = 0;
    for (unsigned j = 0; j < 6; j++) {
        nodes[j].type = OP_IR_VECTOR;
        assert(op_ir_compile(nodes, 9, &target, 1, &source) == OP_VALIDATION_FAILED && !source);
        nodes[j].type = OP_IR_FLOAT;
    }
    puts("PASS test_effects");
    return 0;
}
