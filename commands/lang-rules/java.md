# Java Review Rules

## All Versions

- [ ] No hardcoded credentials, API keys, or connection strings in source code
- [ ] `equals()` and `hashCode()` overridden together — never one without the other
- [ ] `null` not returned from public methods where an empty collection or `Optional` is cleaner
- [ ] Resources (`Connection`, `Stream`, `Reader`) closed in `finally` or via try-with-resources
- [ ] `synchronized` not used on `this` for public classes — use private lock objects
- [ ] `String` concatenation in loops uses `StringBuilder`
- [ ] SQL queries use `PreparedStatement` — no string concatenation with user input
- [ ] Serializable classes define `serialVersionUID` explicitly
- [ ] Collections typed with generics — no raw types (`List` instead of `List<String>`)
- [ ] `instanceof` checks followed immediately by a cast — not separated by unrelated code
- [ ] Checked exceptions not swallowed silently — at minimum logged with stack trace
- [ ] Static utility methods are `static` and not called on instances
- [ ] `final` fields used for values that don't change after construction
- [ ] Thread-shared mutable state access synchronized or uses `java.util.concurrent` types

## Java 8+

- [ ] `Optional` used for return types that may be absent — not null returns from public methods
- [ ] Stream API does not perform side effects in `map()` or `filter()` — use `forEach()` or `peek()` for debugging
- [ ] `Collectors.toUnmodifiableList()` or `List.copyOf()` used when returned collection should not be modified
- [ ] Lambda expressions in `Comparator.comparing()` instead of anonymous `Comparator` classes
- [ ] `CompletableFuture` chains handle exceptions with `.exceptionally()` or `.handle()` — not left unhandled
- [ ] `ConcurrentHashMap` used instead of `Collections.synchronizedMap()` for concurrent access
- [ ] `DateTimeFormatter` and `LocalDate`/`LocalDateTime` used instead of `SimpleDateFormat` and `Date`
- [ ] Stream pipelines terminated — no intermediate operations without a terminal operation
- [ ] `@FunctionalInterface` annotation on interfaces intended as lambda targets

## Java 11+

- [ ] `var` used only where the type is obvious from the right-hand side (improves readability)
- [ ] `String.isBlank()` used instead of `trim().isEmpty()`
- [ ] `Optional.ifPresentOrElse()` used for side-effecting Optional consumption instead of `.isPresent()` + branch
- [ ] HTTP requests use `java.net.http.HttpClient` (not `HttpURLConnection`)
- [ ] `Files.readString()` / `Files.writeString()` used for simple file reads/writes

## Java 17+

- [ ] `sealed` classes / `permits` used to model closed type hierarchies
- [ ] `record` types used for immutable data carriers instead of POJO with boilerplate
- [ ] `switch` expressions used (not statements) where all cases return a value
- [ ] Pattern matching `instanceof` used — `if (obj instanceof String s)` instead of explicit cast
- [ ] Text blocks used for multi-line string literals (SQL, JSON templates)

## Java 21+

- [ ] Virtual threads (`Thread.ofVirtual()`) used for I/O-bound concurrent work, not platform threads
- [ ] `SequencedCollection` methods (`getFirst()`, `getLast()`, `reversed()`) used where applicable
- [ ] Unnamed classes/instance main methods only in scripting contexts — not production code
- [ ] Pattern matching in `switch` guards all cases or has an explicit `default`
- [ ] `StructuredTaskScope` used for structured concurrency — not raw thread + `CompletableFuture` chains
- [ ] `String.formatted()` used instead of `String.format()` for readability in new code
