# Kotlin Review Rules

## All Versions

- [ ] `!!` (not-null assertion) avoided — use safe call `?.`, `let`, or `requireNotNull` with a message
- [ ] Coroutine scopes not leaked — structured concurrency used, scopes tied to lifecycle owners
- [ ] `runBlocking` not called from a coroutine context or main thread in production code
- [ ] `GlobalScope` not used — use `viewModelScope`, `lifecycleScope`, or a DI-provided scope
- [ ] `lateinit var` only on properties initialized before first use — avoid in containers that may be null
- [ ] Data classes not used for entities with business logic — use regular classes
- [ ] `companion object` members that don't access instance state marked `@JvmStatic` in JVM-interop code
- [ ] Sealed classes cover all subclass cases in `when` expressions (compiler-verified exhaustiveness)
- [ ] Extension functions do not access internal implementation details of external types
- [ ] `object` singletons with mutable state document their thread-safety guarantees
- [ ] `with` / `apply` / `run` / `let` / `also` scoping functions used consistently and not nested more than 2 levels
- [ ] Flows collected inside `lifecycleScope.launch { collect {} }` not `GlobalScope`
- [ ] `StateFlow` preferred over `LiveData` in new Kotlin-only code for observable state
- [ ] `Result<T>` used for operations that can fail instead of nullable return + out-of-band error
- [ ] `suspend` functions do not block the calling thread (no `Thread.sleep`, no blocking I/O without `Dispatchers.IO`)
- [ ] `Dispatchers.IO` used for blocking I/O operations, `Dispatchers.Default` for CPU-bound work
- [ ] Coroutine exception handlers (`CoroutineExceptionHandler`) attached to root scopes that launch fire-and-forget coroutines
- [ ] `flow { emit(...) }` builders do not call `emit` from a different coroutine context
- [ ] `SharedFlow` replay and extra buffer capacity set intentionally — default `0` for events, `1` for state
- [ ] `by lazy` properties that access Android context or lifecycle use `LazyThreadSafetyMode.NONE` only when single-threaded access is guaranteed
- [ ] String templates preferred over string concatenation with `+`
- [ ] `when` expressions without an `else` branch on non-sealed types — add exhaustive `else` or convert to sealed hierarchy

## Kotlin 1.5+

- [ ] `value class` used for type-safe wrappers around primitives (replaces inline classes)
- [ ] Sealed interfaces used for event/state hierarchies instead of sealed abstract classes
- [ ] `@JvmRecord` applied to data classes used in Java interop requiring Java records

## Kotlin 2.0+

- [ ] K2 compiler mode implications understood — annotation processor compatibility verified
- [ ] Smart cast improvements (K2) not relied upon in ways that break on older compilers
- [ ] `@Suppress("UNCHECKED_CAST")` replaceable — verify if K2 type inference eliminates the need
- [ ] Multiplatform targets tested — expect/actual declarations verified for all expected platforms
- [ ] `context receivers` (if enabled) used sparingly and documented, as the API is still in flux
