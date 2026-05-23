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

## .NET Framework 4.x

- [ ] C# version limited to 7.3 — no `record` types, `init` properties, `required` keyword, file-scoped namespaces, global usings, switch expressions, pattern matching `is not null`, top-level statements, raw string literals
- [ ] `using` declarations without braces (C# 8) have limited support — prefer explicit `using` blocks
- [ ] `string.Contains(char)` overload not available — use `string.Contains(string)` or `IndexOf`
- [ ] `string.Contains(value, StringComparison)` not available — use `IndexOf(value, comparison) >= 0`
- [ ] `ArgumentNullException.ThrowIfNull()` not available — use manual `if (x == null) throw new ArgumentNullException()`
- [ ] `Span<T>`, `Memory<T>`, `ValueTask` not available or limited — use `byte[]` and `Task`
- [ ] `System.Text.Json` not available — use `Newtonsoft.Json` / `JsonConvert`
- [ ] `IAsyncEnumerable<T>` not available — use `Task<List<T>>` or `Task<IEnumerable<T>>`
- [ ] `ILogger<T>` / `Microsoft.Extensions.Logging` not standard — check if project uses it before suggesting
- [ ] `HttpClientFactory` not available — use `new HttpClient()` with proper disposal or a static instance
- [ ] Null-conditional assignment (`x ??= y`) not available (C# 8+) — use explicit null check
- [ ] `Index` and `Range` types (`^1`, `..`) not available (C# 8+) — use `Length - 1` and `Substring`
- [ ] `IHttpActionResult` is the return type for Web API controllers — not `IActionResult` or `ActionResult<T>`

## EF 6.x

- [ ] No `ExecuteUpdateAsync()` / `ExecuteDeleteAsync()` — use loop + `SaveChanges()` or raw SQL
- [ ] No compiled queries — use parameterized raw SQL for performance-critical paths
- [ ] No `IAsyncEnumerable` query streaming — use `ToListAsync()`
- [ ] No bulk insert/update operations built-in — use `AddOrUpdate()` for seeds, raw SQL for bulk ops
- [ ] Migrations use `DbMigration` base class, not `Migration`
- [ ] `DbContext` uses `DbSet<T>` with `DbModelBuilder` configuration, not `OnModelCreating` fluent API style
- [ ] Connection strings in `Web.config`, not `appsettings.json`
- [ ] `SqlQuery<T>()` for raw SQL queries, not `FromSqlRaw()`
- [ ] No `.AsNoTracking()` on `DbSet` — use `AsNoTracking()` via extension or manual detach
- [ ] `Include()` for eager loading uses string paths (`Include("Orders.Items")`) or lambda — verify overload exists

## ASP.NET Web API 5.x

- [ ] No `[ApiController]` attribute — validation is manual, not automatic
- [ ] No `ActionResult<T>` — use `IHttpActionResult` (`Ok()`, `NotFound()`, `BadRequest()`)
- [ ] No minimal APIs — all endpoints are controller actions
- [ ] No `[FromBody]` auto-binding on complex types — may need explicit `[FromBody]` attribute
- [ ] No built-in model validation auto-response — check `ModelState.IsValid` manually
- [ ] Route templates use `[Route("api/controller/{id}/{action}")]` convention
- [ ] No `ProblemDetails` — return custom error objects or use `BadRequest(ModelState)`
- [ ] `GlobalConfiguration.Configuration.DependencyResolver` for DI — not `IServiceProvider`
- [ ] CORS configured via `EnableCorsAttribute` or `Web.config`, not middleware pipeline
- [ ] No `IOptions<T>` pattern — configuration via `ConfigurationManager` or custom settings classes

## LINQ (All .NET Versions)

- [ ] `IQueryable` queries not materialized prematurely — `.ToList()` only when results are needed
- [ ] `IEnumerable` results not iterated multiple times — materialize with `.ToList()` before re-use
- [ ] `.FirstOrDefault()` / `.SingleOrDefault()` results null-checked before property access
- [ ] `.Where().First()` replaced with `.First(predicate)` for clarity
- [ ] `.Any()` used instead of `.Count() > 0` for existence checks (short-circuits)
- [ ] `.Select()` projections do not include side effects — LINQ should be pure
- [ ] `.OrderBy()` not called multiple times — use `.ThenBy()` for secondary sort
- [ ] `.GroupBy()` results not re-enumerated inside the loop — materialize or use lookup

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
