# C++ Review Rules

## All Versions

- [ ] No raw `new` / `delete` — use `std::unique_ptr`, `std::shared_ptr`, or stack allocation
- [ ] No manual memory management in code that can throw — RAII wrappers used throughout
- [ ] `std::string` not constructed from `nullptr` (undefined behavior)
- [ ] Integer overflow on signed integers considered — use `unsigned` or checked arithmetic for external input
- [ ] Array bounds not accessed out-of-range — prefer iterators or range-based for loops
- [ ] `reinterpret_cast` usage documented with proof that the aliasing is defined behavior
- [ ] No `const_cast` removing constness on values actually declared `const`
- [ ] Virtual destructors on all base classes with virtual methods
- [ ] Rule of Five: if one of destructor, copy ctor, copy assignment, move ctor, move assignment is defined, all five are defined or `= delete`d
- [ ] No `using namespace std;` in header files (pollutes consumer namespaces)
- [ ] Thread-shared mutable data protected with `std::mutex` or `std::atomic`
- [ ] `[[nodiscard]]` on functions whose return value must be checked

## C++14

- [ ] `auto` used for iterator types and complex template types, not for obscuring simple types
- [ ] Generic lambdas used instead of boilerplate `struct` with templated `operator()`
- [ ] `std::make_unique` used instead of `new` with `unique_ptr` constructor

## C++17

- [ ] Structured bindings (`auto [a, b] = pair`) used for multi-value returns
- [ ] `if constexpr` used for compile-time branching in templates instead of specialization
- [ ] `std::optional` used for nullable return values instead of sentinel values or output parameters
- [ ] `std::variant` / `std::visit` used for type-safe unions instead of tagged unions with `void*`
- [ ] Parallel algorithms (`std::sort(std::execution::par, ...)`) used only where thread safety of data is guaranteed
- [ ] `std::string_view` used for read-only string parameters to avoid copies

## C++20

- [ ] Concepts used to constrain template type parameters instead of SFINAE
- [ ] Ranges (`std::ranges::sort`, `std::views::filter`) used for composable sequence operations
- [ ] Coroutines (`co_await`, `co_yield`) require an appropriate promise type — not used ad-hoc
- [ ] `std::span` used instead of pointer + length pairs for buffer parameters

## C++23

- [ ] `std::expected<T, E>` used for error-returning functions instead of exceptions or output parameters
- [ ] `std::mdspan` used for multidimensional array views instead of manual pointer arithmetic
- [ ] `std::print` / `std::println` used in new code instead of `printf`
- [ ] `std::stacktrace` used for capturing call stack in error reporting where diagnostics are needed
- [ ] `if consteval` used to branch compile-time vs runtime evaluation in constexpr functions
