# Swift Review Rules

## All Versions

- [ ] Force unwrap (`!`) not used on optionals from external data — use `guard let` or `if let`
- [ ] `try!` not used in production code — propagate or handle errors explicitly
- [ ] `unowned` references only used when the referenced object's lifetime is guaranteed to exceed the reference
- [ ] Retain cycles in closures avoided — capture lists `[weak self]` or `[unowned self]` used appropriately
- [ ] `@escaping` closures stored weakly when captured by objects that own them
- [ ] `public` API members have documentation comments (`///`)
- [ ] Value types (`struct`, `enum`) preferred over reference types (`class`) for data with no identity
- [ ] Protocol conformances defined in extensions rather than in the primary type declaration
- [ ] `guard` used for early returns on invalid preconditions at function entry
- [ ] `defer` used for guaranteed cleanup in functions with multiple exit points
- [ ] `Hashable` conformance consistent with `Equatable` — equal objects have equal hash values
- [ ] `Codable` types use `CodingKeys` enum to map mismatched JSON field names explicitly
- [ ] `DispatchQueue.main.async` not called from a background thread to update UI (use `@MainActor`)

## Swift 5.5+ (Async/Await, Actors)

- [ ] `async`/`await` used instead of completion handlers for new async APIs
- [ ] `Task` objects not abandoned — cancellation checked via `Task.isCancelled` inside long operations
- [ ] `MainActor` attribute or `@MainActor` annotation used for UI-modifying code
- [ ] Actors used for shared mutable state instead of `DispatchQueue` + mutex
- [ ] `async let` used for concurrent independent work, not sequential `await` chains
- [ ] `TaskGroup` used for dynamic parallel work where the number of tasks is not known at compile time
- [ ] Actor-isolated properties not accessed from synchronous non-actor contexts without `await`

## SwiftUI

- [ ] `@State` used only for local view state — not shared across views
- [ ] `@ObservableObject` / `@StateObject` used for view model lifetime tied to view hierarchy
- [ ] `body` does not have heavy computation — precompute values in view model
- [ ] `List` with large datasets uses `ForEach` with identifiable items, not index-based
- [ ] Animations use `.animation()` / `withAnimation {}` — not abrupt state changes in `onAppear`

## UIKit

- [ ] `viewDidLoad` used for one-time setup, not `viewWillAppear` (called on every presentation)
- [ ] `IBOutlet` connections nil-checked before use in code paths that run before `viewDidLoad`
- [ ] Auto Layout constraints not set on views before they are added to the hierarchy
- [ ] `UITableView` / `UICollectionView` reuse identifiers registered and dequeued correctly — not allocated fresh each `cellForRowAt`
- [ ] Memory warnings (`didReceiveMemoryWarning`) handled by releasing caches and non-visible resources
