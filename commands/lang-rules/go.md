# Go Review Rules

## All Versions

- [ ] All error return values checked — no `_` discarding errors from functions that can fail
- [ ] Errors wrapped with `fmt.Errorf("context: %w", err)` to preserve the error chain
- [ ] `panic` not used for recoverable errors — only for unrecoverable programming mistakes
- [ ] Goroutines have a defined lifetime and are cleaned up (not leaked on function return)
- [ ] `context.Context` threaded through all functions that do I/O or can be cancelled
- [ ] `defer` used for cleanup (file close, mutex unlock) even in error paths
- [ ] Mutex locks not held across `select` or channel sends that could block indefinitely
- [ ] No `init()` functions with side effects that affect global state and complicate testing
- [ ] Interface types defined in the consumer package, not the implementor package
- [ ] Struct fields exported only when they need to be serialized or accessed externally
- [ ] `make([]T, 0, capacity)` used when the slice size is known in advance
- [ ] `sync.WaitGroup` `Add()` called before `go` — never inside the goroutine
- [ ] HTTP response bodies always closed via `defer resp.Body.Close()`
- [ ] `strings.Builder` used for string concatenation in loops — not `+`
- [ ] SQL queries use `database/sql` with `?` / `$N` placeholders — no string formatting with user input
- [ ] Channel directions (`chan<-` / `<-chan`) specified in function signatures to restrict usage
- [ ] `select` with a `default` case used only when non-blocking channel access is explicitly intended
- [ ] `sync.Once` used for one-time initialization instead of flag variables with a mutex
- [ ] Goroutine stacks not grown unboundedly — recursive functions with deep call chains validated
- [ ] `os.Exit` not called inside library code — only acceptable in `main()` after cleanup
- [ ] Table-driven tests (`t.Run`) used for functions with multiple input/output cases

- [ ] Pointer receiver used when method mutates state or receiver is large; value receiver for small immutable types
- [ ] `context.WithCancel` / `context.WithTimeout` results cancelled via `defer cancel()` to avoid context leak
- [ ] No goroutine spawned without a way to signal shutdown — use context cancellation or done channel
- [ ] `http.Client` has explicit `Timeout` set — default is no timeout
- [ ] `json.Decoder` used for streaming/large JSON — `json.Unmarshal` for small known-size payloads
- [ ] Race conditions tested with `go test -race` — enabled in CI

## Go 1.18+

- [ ] Generics used to eliminate code duplication across types, not as over-engineering for single-type cases
- [ ] Generic constraints use `comparable` for map keys and set elements
- [ ] Fuzz tests (`testing.F`) added for functions parsing untrusted external input
- [ ] `any` alias for `interface{}` used consistently in new code

## Go 1.21+

- [ ] `slices.Sort()` / `slices.Contains()` from `slices` package used instead of manual implementations
- [ ] `maps.Keys()` / `maps.Values()` used instead of manual map iteration for key/value extraction
- [ ] `log/slog` used for structured logging in new code replacing `log.Printf`
- [ ] `min()` / `max()` builtins used instead of if-else branches for simple comparisons
- [ ] `errors.Is()` / `errors.As()` used for error type checking — not direct type assertions on errors
