# Task 6 Report: `useApi` polling hook

## Scope

Implemented only the requested dashboard frontend files:

- `dashboard/src/useApi.ts`
- `dashboard/src/useApi.test.ts`

No backend or unrelated frontend files were modified.

## Implementation

`useApi<T>(path, intervalMs)` now:

- Performs an immediate fetch on effect setup.
- Parses successful responses as JSON and stores the typed data.
- Polls with `setInterval` using the supplied interval.
- Captures non-OK responses as `${path} returned ${status}` errors.
- Captures thrown/rejected errors as strings.
- Tracks `loading` until the first completed request.
- Cancels state updates after unmount or dependency changes.
- Clears the polling interval during cleanup.

## Tests

Added four focused tests covering immediate JSON fetch, interval polling,
unmount cleanup, and non-OK response errors.

Command:

```text
cd dashboard && npm test
```

Result: 2 test files passed, 5 tests passed.

## Build

Command:

```text
cd dashboard && npm run build
```

Result: TypeScript check and Vite production build passed.
