# Rust Review Rules

## All Versions

- [ ] `unwrap()` / `expect()` not used in library code or on user-controlled data — propagate errors with `?`
- [ ] `panic!` only used for unrecoverable programming errors, not for expected failure paths
- [ ] `unsafe` blocks include a comment explaining why the invariant is upheld
- [ ] No use of `unsafe` to circumvent borrow checker rules without documenting the lifetime guarantee
- [ ] `Arc<Mutex<T>>` not used where single-threaded `Rc<RefCell<T>>` suffices
- [ ] `clone()` on large data structures in hot paths reviewed — prefer references or `Cow`
- [ ] Public API types implement `Debug` and `Display` where appropriate
- [ ] `From` / `Into` traits implemented for conversions rather than standalone constructor functions
- [ ] Error types implement `std::error::Error` and are composable (support `source()`)
- [ ] `#[derive(Clone, Copy)]` on types that are trivially copyable — not manually implemented
- [ ] Iterators used instead of index-based loops where possible (prevents off-by-one errors)
- [ ] Lifetime annotations on public API functions are minimal and necessary — not over-specified
- [ ] `#[must_use]` attribute on functions whose return value must be checked (e.g., `Result`, `Option`)
- [ ] No integer arithmetic overflow in release mode on values from external input — use `checked_*` or `saturating_*`
- [ ] `std::mem::forget()` / `ManuallyDrop` usage documented with an explanation of the intended invariant

## Edition 2021

- [ ] Disjoint capture in closures understood — closures capture individual fields, not the whole struct
- [ ] `IntoIterator` implementations on arrays used instead of `.iter()` where ownership is intended
- [ ] Pattern `or` syntax (`A | B` in `match` arms) used to reduce duplicated match arm bodies
- [ ] `std::panic::catch_unwind` used where FFI boundary requires catching panics
- [ ] `Rc`/`RefCell` borrow panics avoided — `try_borrow()` used in contexts where runtime panics are unacceptable
- [ ] `Box<dyn Error>` used only for application-level error aggregation — library code uses concrete error types
- [ ] Trait objects (`dyn Trait`) vs generics decision documented when performance matters

## Edition 2024

- [ ] `gen` blocks / generators used for lazy iteration where previously `Iterator` boilerplate was needed
- [ ] `async` closures (stable in 2024) used instead of `async move {}` workarounds
- [ ] `let...else` chains preferred over deeply nested `if let` for early-return guards
- [ ] Cargo resolver v3 behavior verified — dependency resolution changes do not introduce unexpected feature unification
- [ ] `unsafe extern` blocks explicitly list safe vs unsafe functions (2024 edition requirement)
- [ ] `#[expect(lint)]` used instead of `#[allow(lint)]` where the suppression should be temporary
- [ ] `Mutex::new` in const context used for static mutexes instead of `lazy_static!` or `once_cell`
- [ ] `std::hint::assert_unchecked` used only in code where the invariant is provably upheld
