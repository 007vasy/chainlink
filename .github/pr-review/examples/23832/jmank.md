# jmank88 inline comments on smartcontractkit/chainlink#23832

## `core/capabilities/remote/trigger_publisher.go:933`

Is it OK that a double close will always return nil? or should that be an error?

## `core/capabilities/launcher.go:87`

nit/ "key" seems redundant for most usages - wdyt about `shimID`? 🤷 
```suggestion
type shimID struct {
```

## `core/capabilities/launcher.go:889`

```suggestion
			w.subServices = slices.Delete(w.subServices, i, i+1)
```

## `core/capabilities/remote/trigger_publisher.go:933`

Sounds good. If you still wanted to shake out the double closes in tests, you could check `testing.Testing()`, but nbd.
