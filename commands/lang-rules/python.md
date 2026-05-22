# Python Review Rules

## All Versions

- [ ] No hardcoded credentials or secrets — use environment variables or secrets manager
- [ ] `except Exception` not used as a bare catch-all without re-raise or detailed logging
- [ ] Mutable default arguments not used (`def f(items=[])`) — use `None` and initialize inside body
- [ ] `os.system()` / `subprocess.run(shell=True)` not used with user-controlled input (command injection)
- [ ] SQL queries use parameterized form — no f-string or `%` formatting of query strings with user data
- [ ] File paths from user input sanitized before use (`pathlib.Path` + explicit suffix/parent checks)
- [ ] Context managers (`with`) used for file handles, DB connections, locks
- [ ] `isinstance()` used for type checks, not `type() ==`
- [ ] Generators preferred over list comprehensions for large sequences consumed once
- [ ] Circular imports avoided — modules organized so lower-level modules do not import higher-level ones
- [ ] `__all__` defined on public modules to control exported names
- [ ] `logging` used instead of `print()` in library/production code
- [ ] Regular expressions compiled with `re.compile()` when used in a loop
- [ ] `dict.get(key, default)` used instead of try/except KeyError for optional dict access
- [ ] Thread-shared mutable state protected with `threading.Lock`

## Python 3.8+

- [ ] Walrus operator (`:=`) used for clarity, not in complex nested expressions that obscure control flow
- [ ] `typing` annotations present on all public function signatures
- [ ] `TypedDict` used for dict-typed parameters/returns rather than bare `Dict[str, Any]`
- [ ] `Protocol` used for structural subtyping instead of ABC when no state is shared

## Python 3.10+

- [ ] `match` / `case` (structural pattern matching) used for exhaustive variant dispatch
- [ ] Union types written as `X | Y` instead of `Union[X, Y]` in annotations
- [ ] `ParamSpec` used when decorators must preserve callable signatures

## Python 3.12+

- [ ] `type` statement used for type aliases instead of `TypeAlias` assignment
- [ ] `@override` decorator applied to methods that override base class implementations
- [ ] Generic classes use PEP 695 `class Foo[T]:` syntax rather than `Generic[T]` where supported
- [ ] `sys.monitoring` used for performance-sensitive profiling hooks instead of `sys.settrace`
- [ ] f-string nesting and complex expressions inside `{}` kept readable — extract to a variable if needed
- [ ] `ExceptionGroup` and `except*` used when multiple unrelated exceptions can be raised concurrently (e.g., in `asyncio.TaskGroup`)
- [ ] `pathlib.Path` used instead of `os.path` string manipulation for file path operations
- [ ] `dataclasses.field(default_factory=...)` used for mutable defaults in `@dataclass` fields
