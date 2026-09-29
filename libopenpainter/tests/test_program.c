#include "openpainter/op_program.h"
#include <assert.h>
#include <math.h>
#include <string.h>
int main(void) {
    op_ir_node_t nodes[2] = {0};
    op_ir_target_t target = {1, 0, 0};
    op_program_t *p, *q;
    uint64_t revision;
    uint32_t count;
    nodes[0].opcode = OP_IR_PARAMETER;
    nodes[0].type = OP_IR_FLOAT;
    nodes[1].opcode = OP_IR_CONVERT;
    nodes[1].type = OP_IR_VECTOR;
    assert(op_program_create(nodes, 2, &target, 1, 1, 0, &p) == OP_OK);
    assert(op_program_create(nodes, 2, &target, 1, 1, 0, &q) == OP_OK);
    assert(op_program_identity(p) != op_program_identity(q));
    op_program_destroy(q);
    memset(nodes, 0, sizeof(nodes));
    assert(strstr(op_program_source(p), "op_params.p[0].x"));
    float value = .25f;
    assert(op_program_update(p, &value, 1, NULL, 0, &revision) == OP_OK && revision == 1);
    const float *data = op_program_uniforms(p, &count);
    assert(count == 4 && data[0] == value && data[1] == 0);
    assert(op_program_update(p, &value, 1, NULL, 0, &revision) == OP_OK && revision == 1);
    value = NAN;
    assert(op_program_update(p, &value, 1, NULL, 0, &revision) == OP_VALIDATION_FAILED &&
           data[0] == .25f);
    value = .75f;
    assert(op_program_update(p, &value, 1, NULL, 0, &revision) == OP_OK && revision == 2 &&
           data[0] == .75f);
    op_program_destroy(p);
    return 0;
}
