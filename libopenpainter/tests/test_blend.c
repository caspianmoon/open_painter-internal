#include "openpainter/op_ir.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>

int main(void) {
    const char *modes[] = {
        "NORMAL",         "DISABLE",     "MULTIPLY",   "ADD",           "SUBTRACT",   "DARKEN",
        "LIGHTEN",        "DIVIDE",      "DIFFERENCE", "EXCLUSION",     "SCREEN",     "LINEAR_BURN",
        "COLOR_BURN",     "COLOR_DODGE", "OVERLAY",    "HARD_LIGHT",    "SOFT_LIGHT", "VIVID_LIGHT",
        "LINEAR_LIGHT",   "PIN_LIGHT",   "HUE",        "SATURATION",    "COLOR",      "LUMINOSITY",
        "NORMAL_COMBINE", "RNM",         "NORMAL_ADD", "NORMAL_REPLACE"};
    op_ir_node_t nodes[8] = {0};
    for (unsigned i = 0; i < 5; i++) {
        nodes[i].opcode = OP_IR_CONSTANT;
        nodes[i].type = i == 0 || i == 2 ? OP_IR_VECTOR : OP_IR_FLOAT;
        nodes[i].value[0] = nodes[i].value[1] = nodes[i].value[2] = .5;
        nodes[5].inputs[i] = i;
    }
    nodes[5].opcode = OP_IR_BLEND;
    nodes[5].type = OP_IR_COLOR;
    nodes[6].opcode = OP_IR_CONVERT;
    nodes[6].type = OP_IR_VECTOR;
    nodes[6].inputs[0] = 5;
    nodes[7].opcode = OP_IR_COMPONENT;
    nodes[7].type = OP_IR_FLOAT;
    nodes[7].inputs[0] = 5;
    nodes[7].aux[0] = 3;
    op_ir_target_t target = {6, 7, 0};
    char *source = NULL;
    for (unsigned i = 0; i < sizeof(modes) / sizeof(modes[0]); i++) {
        snprintf(nodes[5].operation, sizeof nodes[5].operation, "%s", modes[i]);
        for (unsigned opaque = 0; opaque < 2; opaque++) {
            nodes[5].aux[0] = opaque;
            assert(op_ir_compile(nodes, 8, &target, 1, &source) == OP_OK);
            assert(strstr(source, "vec4 v5 = op_blend(v0, v1, v2, v3, v4,"));
            op_ir_free(source);
        }
    }
    for (unsigned i = 0; i < 5; i++) {
        nodes[5].inputs[i] = 5;
        assert(op_ir_compile(nodes, 8, &target, 1, &source) == OP_VALIDATION_FAILED && !source);
        nodes[5].inputs[i] = i;
        unsigned type = nodes[i].type;
        nodes[i].type = type == OP_IR_FLOAT ? OP_IR_VECTOR : OP_IR_FLOAT;
        assert(op_ir_compile(nodes, 8, &target, 1, &source) == OP_VALIDATION_FAILED && !source);
        nodes[i].type = type;
    }
    nodes[5].type = OP_IR_VECTOR;
    assert(op_ir_compile(nodes, 8, &target, 1, &source) == OP_VALIDATION_FAILED && !source);
    nodes[5].type = OP_IR_COLOR;
    nodes[5].aux[0] = 2;
    assert(op_ir_compile(nodes, 8, &target, 1, &source) == OP_VALIDATION_FAILED && !source);
    nodes[5].aux[0] = 0;
    snprintf(nodes[5].operation, sizeof nodes[5].operation, "%s", "INJECT();");
    assert(op_ir_compile(nodes, 8, &target, 1, &source) == OP_UNSUPPORTED && !source);
    memset(nodes[5].operation, 'a', sizeof(nodes[5].operation));
    assert(op_ir_compile(nodes, 8, &target, 1, &source) == OP_VALIDATION_FAILED && !source);
    target.color = 0;
    target.alpha = 1;
    assert(op_ir_compile(nodes, 5, &target, 1, &source) == OP_OK);
    assert(!strstr(source, "op_blend"));
    op_ir_free(source);
    puts("PASS test_blend");
    return 0;
}
