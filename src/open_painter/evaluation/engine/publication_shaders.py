"""Independent reference for fused, exact box-filter mip publication shaders."""


def mip_batch_source(source, count):
    if count not in (2, 3):
        raise ValueError('A mip batch needs two or three channels')
    bodies = []
    for index in range(count):
        body = source.replace('void main()', '')
        body = body.replace('op_source', f'op_source_{index}')
        body = body.replace('op_out0', f'op_out{2 * index}')
        body = body.replace('op_out1', f'op_out{2 * index + 1}')
        bodies.append(body)
    return 'void main() {\n' + '\n'.join(bodies) + '\n}\n'
