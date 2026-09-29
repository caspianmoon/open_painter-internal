/* ABI version, capabilities, context lifecycle, error reporting. */
#include <stdio.h>
#include <string.h>

#include "openpainter/op_api.h"

#define CHECK(cond)                                                                                \
    do {                                                                                           \
        if (!(cond)) {                                                                             \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);                                 \
            return 1;                                                                              \
        }                                                                                          \
    } while (0)

int main(void) {
    op_context_t *ctx = NULL;
    op_capabilities_t caps;
    char message[256];
    op_metrics_t metrics;

    CHECK(op_get_abi_version() == OP_ABI_VERSION);
    CHECK(op_context_create(NULL) == OP_INVALID_ARGUMENT);
    CHECK(op_query_capabilities(NULL, &caps) == OP_INVALID_ARGUMENT);

    CHECK(op_context_create(&ctx) == OP_OK);
    CHECK(ctx != NULL);
    CHECK(op_query_capabilities(ctx, &caps) == OP_OK);
    CHECK(caps.abi_version == op_get_abi_version());
    CHECK(caps.max_texture_size == 8192u);
    CHECK(caps.has_gpu == 0u);

    /* Error path records a message retrievable through the API. */
    CHECK(op_document_create(ctx, 0u, NULL) == OP_INVALID_ARGUMENT);
    CHECK(op_copy_last_error(ctx, message, sizeof(message)) == OP_OK);
    CHECK(strlen(message) > 0u);

    CHECK(op_get_metrics(ctx, &metrics) == OP_OK);
    CHECK(metrics.documents_created == 0u);
    CHECK(op_get_metrics(NULL, &metrics) == OP_INVALID_ARGUMENT);

    op_context_destroy(ctx);
    op_context_destroy(NULL); /* must tolerate null */
    printf("PASS test_abi\n");
    return 0;
}
