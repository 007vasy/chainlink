# jmank88 inline comments on smartcontractkit/chainlink#23878

## `core/services/workflows/v2/triggers/coordinator.go:363`

This logic could be tucked away in a method on `*workflowTriggers`:
```suggestion
	select {
	case <-wt.drained():
```

## `core/services/workflows/v2/triggers/coordinator.go:210`

This logic looks fine currently, but I'm worried that it is fragile and could be messed up in future changes. What do you think about deriving this from named return vars instead? Meaning we name `err error` in the func signature, and then we just check that value from the defer:
```suggestion
	defer func() {
		if err != nil {
			c.freeWorkflowLimit(ctx, lggr)
		}
	}()
```

## `core/services/workflows/v2/triggers/coordinator.go:398`

Would a reader/writer mutex make sense here? For `get` and `pending`?
```suggestion
	mu        sync.RWMutex
```

## `core/services/workflows/v2/triggers/coordinator.go:238`

nit/ I wonder if moving this to a method on cw would simplify things overall? You could include the context logic which is nice, and some of the params are already fields on cw, but you would have to pass others, including, some kind of reference back to `c.eng` in order to call `GoCtx`, so it might not be worth it 🤷 
```suggestion
	cw.goReadEvents(context.WithoutCancel(ctx), c.eng.GoCtx, eventChans, ....)
```

## `core/services/workflows/v2/triggers/coordinator.go:398`

That could work, although the range loop is a less strict view of the state:
> Range does not necessarily correspond to any consistent snapshot of the Map's contents: no key will be visited more than once, but if the value for any key is stored or deleted concurrently (including by f), Range may reflect any mapping for that key from any point during the Range call. Range does not block other methods on the receiver; even f itself may call any method on m.

https://pkg.go.dev/sync#Map.Range
