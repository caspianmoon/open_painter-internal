#include "openpainter/op_ir.h"
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
int main(void) {
    op_ir_node_t nodes[5] = {0};
    char *source = NULL;
    nodes[0].opcode = OP_IR_CONSTANT;
    nodes[0].type = OP_IR_FLOAT;
    nodes[0].value[0] = .25;
    nodes[1] = nodes[0];
    nodes[1].value[0] = .75;
    nodes[2].opcode = OP_IR_MATH;
    nodes[2].type = OP_IR_FLOAT;
    nodes[2].inputs[0] = 0;
    nodes[2].inputs[1] = 1;
    nodes[2].inputs[2] = 0;
    snprintf(nodes[2].operation, sizeof nodes[2].operation, "%s", "MULTIPLY");
    nodes[3].opcode = OP_IR_CONVERT;
    nodes[3].type = OP_IR_VECTOR;
    nodes[3].inputs[0] = 2;
    op_ir_target_t target = {3, 1, 0};
    assert(op_ir_compile(nodes, 4, &target, 1, &source) == OP_OK && source);
    assert(strstr(source, "float v2 = (v0 * v1);") && strstr(source, "op_out0 = vec4(v3, v1)"));
    op_ir_free(source);
    nodes[2].inputs[0] = 2;
    assert(op_ir_compile(nodes, 4, &target, 1, &source) == OP_VALIDATION_FAILED && !source);
    nodes[2].inputs[0] = 0;
    nodes[0].value[0] = NAN;
    assert(op_ir_compile(nodes, 4, &target, 1, &source) == OP_VALIDATION_FAILED && !source);
    nodes[0].value[0] = .25;
    snprintf(nodes[2].operation, sizeof nodes[2].operation, "%s", "NOT_SUPPORTED");
    assert(op_ir_compile(nodes, 4, &target, 1, &source) == OP_UNSUPPORTED && !source);
    nodes[4].opcode = OP_IR_PROCEDURAL;
    nodes[4].type = OP_IR_FLOAT;
    nodes[4].inputs[0] = 3;
    nodes[4].inputs[1] = nodes[4].inputs[2] = nodes[4].inputs[3] = 0;
    snprintf(nodes[2].operation, sizeof nodes[2].operation, "%s", "MULTIPLY");
    snprintf(nodes[4].operation, sizeof nodes[4].operation, "%s", "CELLULAR");
    target.color = 4;
    target.layout = 2;
    assert(op_ir_compile(nodes, 5, &target, 1, &source) == OP_OK &&
           strstr(source, "op_procedural_cellular"));
    assert(!strstr(source, "op_procedural_fingerprints"));
    op_ir_free(source);
    target.alpha = 10;
    assert(op_ir_compile(nodes, 5, &target, 1, &source) == OP_VALIDATION_FAILED && !source);
    puts("PASS test_ir");
    return 0;
}
