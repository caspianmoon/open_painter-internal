#ifndef OP_IR_RULES_H
#define OP_IR_RULES_H
#include "openpainter/op_ir.h"
/* Input slots each opcode reads. Shared by code generation and IR analysis so
 * both always agree on which earlier nodes a node depends on. */
static inline unsigned op_ir_arity(uint32_t code) {
    switch (code) {
    case OP_IR_CONVERT:
    case OP_IR_COMPONENT:
    case OP_IR_IMAGE:
    case OP_IR_IMAGE_COLOR:
    case OP_IR_OBJECT:
        return 1;
    case OP_IR_MATH:
    case OP_IR_COMBINE:
        return 3;
    case OP_IR_VECTOR_MATH:
    case OP_IR_PROCEDURAL:
        return 4;
    case OP_IR_NORMAL_MAP:
    case OP_IR_BLEND:
        return 5;
    case OP_IR_MASK_FOLD:
        return 6;
    case OP_IR_EFFECT:
        return 8;
    default:
        return 0;
    }
}
#endif
