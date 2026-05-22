# C# Review Rules

## All Versions

- [ ] No hardcoded credentials, connection strings, or secrets in source code
- [ ] Exceptions are not swallowed silently (`catch (Exception) {}`)
- [ ] `IDisposable` types used in `using` statements or disposed explicitly
- [ ] `async void` methods only used for event handlers — never for fire-and-forget without error handling
- [ ] Nullable reference type annotations (`?`) present where nulls are valid inputs
- [ ] No use of `Thread.Sleep` on async code paths — use `Task.Delay`
- [ ] `ConfigureAwait(false)` used in library code to avoid deadlocks
- [ ] `string` concatenation inside loops uses `StringBuilder`, not `+`
- [ ] `==` not used to compare strings where culture-sensitive comparison is needed
- [ ] Static mutable state (static fields/properties) is thread-safe
- [ ] LINQ queries do not evaluate multiple times (materialize with `.ToList()` before re-use)
- [ ] No `dynamic` type used in performance-critical paths
- [ ] `public` API surfaces have XML documentation on all members
- [ ] Interfaces used for dependencies rather than concrete types (DI-friendly)
- [ ] No reflection over private members of external types
- [ ] Enum flags decorated with `[Flags]` attribute when bit-masking is intended

## .NET 6+

- [ ] `DateOnly` and `TimeOnly` used instead of `DateTime` for date-only or time-only values
- [ ] `ArgumentNullException.ThrowIfNull()` used instead of manual null guard code
- [ ] Minimal API endpoints validate inputs before processing
- [ ] `ILogger<T>` injected via DI, not instantiated directly
- [ ] Hot reload–incompatible patterns (source generators, static constructors with side effects) documented
- [ ] `CancellationToken` threaded through async call chains from entry points
- [ ] Record types used for immutable data transfer objects where appropriate

## .NET 8+

- [ ] `FrozenDictionary` / `FrozenSet` used for large, read-only lookup tables
- [ ] Primary constructors used consistently for simple classes/structs without extra initialization
- [ ] `TimeProvider` abstracted for testable time-dependent logic
- [ ] Keyed services used where multiple implementations of the same interface are registered
- [ ] `SearchValues<T>` used for high-frequency char/byte search patterns in hot paths

## EF Core 6+

- [ ] No N+1 queries — related data loaded with `.Include()` or projection
- [ ] Read-only queries use `.AsNoTracking()`
- [ ] Bulk deletes/updates use `ExecuteDeleteAsync()` / `ExecuteUpdateAsync()` not loop + SaveChanges
- [ ] Migrations do not drop columns that are still referenced in code
- [ ] `DbContext` not shared across async paths (not thread-safe)
- [ ] Raw SQL (`FromSqlRaw`) uses parameterized queries — no string interpolation

## ASP.NET Core

- [ ] `[Authorize]` or `[AllowAnonymous]` explicitly on all controller actions — no implicit open endpoints
- [ ] `ModelState.IsValid` checked before processing in controller actions
- [ ] CORS policy explicitly defined — no wildcard `*` origin in production config
- [ ] Response caching headers set appropriately on read endpoints
- [ ] File uploads validate extension, MIME type, and maximum size
- [ ] Anti-forgery tokens validated on state-changing form actions
