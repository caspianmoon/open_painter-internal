#include "openpainter/op_ir.h"
#include <stddef.h>
#include <string.h>

#include "fixed.inc"
const char *op_shader_source(const char *name) {
    if (!name)
        return NULL;
    for (size_t i = 0; fixed_shaders[i].name; i++)
        if (!strcmp(name, fixed_shaders[i].name))
            return fixed_shaders[i].source;
    return NULL;
}
