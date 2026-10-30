# PR #23837 - [CRE-6745] wire noop coordinator + feature flag

- URL: https://github.com/smartcontractkit/chainlink/pull/23837
- Base: `3e6f5a66fef328353818cf22a9e15706ebcb3106`
- Head: `f9b4c8367b58a0cb50bb36098976a063c3f42ee8`
- Inline findings: 5

<!-- pr-coding-standards-review -->

**Coding-standards review**

Reviewed `smartcontractkit/chainlink#23837` against `.github/copilot-instructions.md` at `develop`.

5 inline finding(s).

## Inline comments

### `core/services/workflows/v2/config.go:405` (RIGHT)

**High** - Lifecycle

the new `CoordinatedEngine` gate limiter is never closed: `EngineFeatureFlags.Close()` (config.go:415-420) closes only `WorkflowTagBackfill`, while the analogous `CentralizedWorkflowOwnerVerificationEnabled` gate limiter is closed in `EngineLimiters.Close()` (config.go:378); additionally, if `MakeGateLimiter` here fails, the already-created `workflowTagBackfill` is leaked since the error return skips cleanup

**Standard:** "Is everything acquired or started (contexts, goroutines, connections, handles) released or stopped on every path, including errors and shutdown?"

**Fix:** close both limiters in `EngineFeatureFlags.Close()` (e.g. `services.CloseAll(e.CoordinatedEngine, e.WorkflowTagBackfill)`) and release `workflowTagBackfill` on the `MakeGateLimiter` error path.

### `core/services/workflows/syncer/v2/trigger_coordinator.go:100` (RIGHT)

**High** - Correctness

`noopTriggerCoordinator.UnregisterTriggers` always returns nil although the interface it implements promises "Returns ErrWorkflowNotCoordinated if workflowID was never registered here" (line 52); the noop registers nothing, so by that contract every call should return the sentinel, making `ErrWorkflowNotCoordinated` (line 17) unreachable from the repo's only implementation

**Standard:** "Does the code do what the PR claims, for every input and state it can receive, including edge, empty, and failure cases?"

**Fix:** return `ErrWorkflowNotCoordinated` from the noop (callers at handler.go:1053/1188 already suppress it) or amend the interface doc to state the no-op always returns nil.

### `core/services/workflows/syncer/v2/handler.go:1317` (RIGHT)

**Medium** - Design

`WorkflowDonConfigVersion` is hardcoded to `1`, duplicating `pinnedWorkflowDonConfigVersion = 1` (base_engine.go:65) with a comment admitting it is "mirrored here since the syncer can't reference that unexported v2 constant"; if v2 ever bumps the pin, legacy and coordinated paths silently emit different DON config versions in trigger metadata

**Standard:** "Does each design decision (format, algorithm, storage) live in one place, or must several packages change together?"

**Fix:** export the constant from `workflows/v2` (e.g. `PinnedWorkflowDonConfigVersion`) and reference it here instead of the literal.

### `core/services/workflows/syncer/v2/handler.go:1187` (RIGHT)

**Medium** - Design

the coordinator-unregister block (1187-1191) is a verbatim copy of stopEngine's (1052-1056), and its own comment (1183-1186) says the path "must mirror stopEngine's coordinator handling, or trigger registrations are left orphaned on this path alone"; a future edit to one copy (e.g. changing the error-suppression filter) that misses the other orphans registrations on one stop path

**Standard:** "Is each piece of knowledge represented once?"

**Fix:** extract a shared `eventHandler` helper (e.g. `unregisterCoordinatedTriggers(ctx, workflowID)`) and call it from both stopEngine and tryEngineCleanup.

### `core/services/workflows/syncer/v2/trigger_coordinator.go:70` (RIGHT)

**Medium** - Tests

the new public `NewTriggerCoordinator`/`noopTriggerCoordinator` (RegisterTriggers, Ack, UnregisterTriggers, Start warning) has no test; handler tests only exercise `recordingCoordinator` fakes, so the noop's contract is unpinned and a regression (e.g. Ack returning an error) would pass CI

**Standard:** "Is new behaviour tested through its public interface, including error paths?"

**Fix:** add a focused test driving the real coordinator through RegisterTriggers/Ack/UnregisterTriggers and asserting its log-and-no-op contract.
