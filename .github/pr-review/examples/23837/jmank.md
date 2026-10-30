# jmank88 inline comments on smartcontractkit/chainlink#23837

## `core/services/workflows/v2/config.go:406`

Initializing these manually is not usually recommended. Can we reference the package defaults directly instead?
```suggestion
	coordinatedEngine, err := limits.MakeGateLimiter(lf, cresettings.Default.CoordinatedEngineEnabled)
```

## `core/services/workflows/syncer/v2/handler.go:886`

Is it risky to use a bool here rather than a time stamp range?

## `core/services/workflows/syncer/v2/handler.go:1358`

You could package up this block so that you don't have to repeat it:
```suggestion
	cleanup := func () {
		if _, innerErr := h.engineRegistry.Pop(in.wid); innerErr != nil {
			h.lggr.Warnw("Failed to pop engine from registry after local node resolution failure", "workflowID", in.wid.Hex(), "err", innerErr)
		}
		if innerErr := engine.Close(); innerErr != nil {
			h.lggr.Warnw("Failed to close engine after local node resolution failure", "workflowID", in.wid.Hex(), "err", innerErr)
		}
	}

	// Registration happens here, now that the engine is already in the
	// registry, so the coordinator's readers can resolve it for the first
	// event. RegisterTriggers calls the engine's Subscribe itself — the WASM
	// call is scoped to the one workflow it describes, which matters because
	// Handle runs on a worker pool and several workflows reach this point
	// concurrently.
	localNode, err := h.capRegistry.LocalNode(ctx)
	if err != nil {
		cleanup()
		return fmt.Errorf("failed to resolve local node DON ID for trigger registration: %w", err)
	}
	donID := localNode.WorkflowDON.ID

	triggerIDs, err := h.triggerCoordinator.RegisterTriggers(ctx, engine, RegistrationParams{
		WorkflowOwner:       spec.WorkflowOwner,
		WorkflowName:        in.workflowName.Hex(),
		DecodedWorkflowName: in.workflowName.String(),
		WorkflowTag:         spec.WorkflowTag,
		WorkflowDonID:       donID,
		// pinnedWorkflowDonConfigVersion in v2 pins this to 1 to avoid forcing
		// forwarder updates on config churn; mirrored here since the syncer
		// can't reference that unexported v2 constant.
		WorkflowDonConfigVersion:      1,
		WorkflowRegistryChainSelector: h.workflowRegistryChainSelector,
		WorkflowRegistryAddress:       h.workflowRegistryAddress,
	})
	if err != nil {
		cleanup()
```
