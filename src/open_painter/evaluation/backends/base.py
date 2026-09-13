"""Bounded experimental image-chain contract; CPU remains the default oracle."""
SUPPORTED=frozenset({'FILL','OVER','LEVELS','GAUSSIAN_BLUR'})


def validate(operations):
    for operation in operations:
        if operation['kind'] not in SUPPORTED:
            raise ValueError('Operation is outside the experimental backend slice')
        allowed={'FILL':{'color'},'OVER':{'opacity'},'LEVELS':{'black','white','gamma'},'GAUSSIAN_BLUR':{'radius'}}
        if set(operation)-allowed[operation['kind']]-{'kind'}:
            raise ValueError('Parameters are outside the experimental backend slice')
        if operation['kind']=='GAUSSIAN_BLUR' and not 0<operation.get('radius',2)<=8:
            raise ValueError('Experimental blur supports radius (0, 8]')
        if operation['kind']=='LEVELS' and (operation.get('white',1)<=operation.get('black',0) or operation.get('gamma',1)<=0):
            raise ValueError('Invalid experimental Levels parameters')
