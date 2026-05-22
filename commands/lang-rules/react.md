# React Review Rules

## React 16

- [ ] `componentDidMount` / `componentDidUpdate` / `componentWillUnmount` lifecycle methods balance subscriptions with cleanup
- [ ] `setState` in `componentDidUpdate` guarded with a condition to avoid infinite update loops
- [ ] Error boundaries (`componentDidCatch`) present around UI sections that render user data
- [ ] `key` prop on list items uses a stable unique ID, not array index when list is reordered/filtered
- [ ] `propTypes` or TypeScript types defined for all component props

## React 17+

- [ ] `useEffect` cleanup function cancels subscriptions, timers, and async operations
- [ ] `useEffect` dependency array includes all variables from the enclosing scope that are used inside
- [ ] `useCallback` / `useMemo` used only when the memoized value is passed to `React.memo` children or in hot render paths — not applied indiscriminately
- [ ] `useState` setter called with a function form (`setState(prev => ...)`) when new state depends on previous
- [ ] Custom hooks follow `use*` naming convention and only call other hooks at the top level
- [ ] `useRef` used for values that must persist across renders without triggering re-renders
- [ ] No direct DOM manipulation (`document.getElementById`) inside render — use refs
- [ ] Controlled inputs pair `value` with `onChange` — never `value` without `onChange`
- [ ] Context values memoized (`useMemo`) to avoid re-rendering all consumers on every parent render
- [ ] `useReducer` used instead of multiple related `useState` calls when state transitions are complex

## React 18+

- [ ] `startTransition` wraps non-urgent state updates to keep the UI responsive during heavy renders
- [ ] `useDeferredValue` used to debounce expensive derived computations from user input
- [ ] Concurrent-mode–safe effects do not rely on `useLayoutEffect` for data fetching
- [ ] `Suspense` boundaries placed at meaningful loading-state granularity — not wrapping entire pages
- [ ] `use()` hook (data fetching) only called inside `Suspense`-wrapped trees
- [ ] Server Components do not import client-only code (event handlers, browser APIs, stateful hooks)
- [ ] Client Components marked with `"use client"` directive at the top of the file
- [ ] `useId()` used for generating stable IDs for accessibility attributes (`aria-describedby`, `htmlFor`)
- [ ] `act()` wraps all state updates in tests to prevent async act warnings
- [ ] `React.StrictMode` enabled in development to surface double-invocation bugs
- [ ] `flushSync` used only when synchronous DOM reads are required after a state update — not as a general pattern
- [ ] Context `Provider` `value` object memoized to avoid unnecessary re-renders on every parent render cycle
- [ ] `useOptimistic` used for optimistic UI updates that may need to roll back on server error
- [ ] Error boundaries reset their state when `key` changes — not left in error state after navigation
- [ ] `React.lazy()` combined with `Suspense` for code-split components — not dynamic imports without a boundary
