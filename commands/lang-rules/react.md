# React Review Rules

## React 16

### Available patterns
- [ ] `componentDidMount` / `componentDidUpdate` / `componentWillUnmount` lifecycle methods balance subscriptions with cleanup
- [ ] `setState` in `componentDidUpdate` guarded with a condition to avoid infinite update loops
- [ ] Error boundaries (`componentDidCatch`) present around UI sections that render user data
- [ ] `key` prop on list items uses a stable unique ID, not array index when list is reordered/filtered
- [ ] `propTypes` or TypeScript types defined for all component props
- [ ] Hooks (`useState`, `useEffect`, `useCallback`, `useMemo`, `useRef`, `useReducer`, `useContext`) available from React 16.8+ — prefer functional components with hooks over class components
- [ ] `useSelector` / `useDispatch` available from react-redux 7.1+ — verify project has react-redux 7+
- [ ] `React.memo()` for preventing unnecessary re-renders on pure functional components
- [ ] `React.lazy()` with `Suspense` for code-splitting (React 16.6+)
- [ ] `React.createContext` / `useContext` for prop drilling avoidance

### DO NOT suggest (not available in React 16)
- [ ] No `useId()` — generate IDs manually or use a counter
- [ ] No `useTransition()` / `startTransition()` — no concurrent rendering
- [ ] No `useDeferredValue()` — use manual debounce patterns
- [ ] No `useSyncExternalStore()` — use `useEffect` + `useState` for external store subscriptions
- [ ] No `use()` hook for data fetching — use `useEffect` + `useState`
- [ ] No `useOptimistic()` — implement optimistic updates manually with `useState`
- [ ] No automatic batching of state updates outside React event handlers — only batched inside React events
- [ ] No `createRoot()` / `hydrateRoot()` — use `ReactDOM.render()` and `ReactDOM.hydrate()`
- [ ] No Server Components or `"use client"` / `"use server"` directives
- [ ] No `Suspense` for data fetching — `Suspense` only works with `React.lazy()` in React 16
- [ ] No `flushSync` — state updates are always synchronous in React event handlers

### React Router v5 (common with React 16 projects)
- [ ] Uses `<Switch>` not `<Routes>` — `<Routes>` is react-router v6+
- [ ] Uses `useHistory()` not `useNavigate()` — `useNavigate()` is v6+
- [ ] Uses `<Route component={X}>` or `<Route render={fn}>` not `<Route element={<X/>}>`
- [ ] No `<Outlet>` or nested route elements — use render props or component prop
- [ ] `useParams()`, `useLocation()`, `useRouteMatch()` available in v5

### Redux 3.x-4.x (common with React 16 projects)
- [ ] No Redux Toolkit (`createSlice`, `createAsyncThunk`, `configureStore`, RTK Query)
- [ ] Uses `createStore()` with manual reducers and action types
- [ ] Uses `combineReducers()` for reducer composition
- [ ] Middleware via `applyMiddleware()` — not `configureStore` middleware option
- [ ] Side effects via redux-saga or redux-thunk — not RTK Query

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

## Accessibility (All React Versions)

- [ ] Interactive elements (`button`, `a`, `input`) used for clickable content — not `div` or `span` with `onClick`
- [ ] `aria-label` or `aria-labelledby` on icon-only buttons and links
- [ ] `alt` text on all `<img>` elements — empty `alt=""` for decorative images
- [ ] Form inputs have associated `<label>` via `htmlFor` — not placeholder-only labels
- [ ] `role` attribute not used to override semantic HTML — use the correct element instead
- [ ] `aria-hidden="true"` on decorative icons and elements not relevant to screen readers
- [ ] Keyboard navigation works for all interactive flows — `onKeyDown` handler alongside `onClick`
- [ ] Focus management after modal open/close — focus trapped in modal, restored on close
- [ ] `tabIndex` not set to positive values — only `0` (natural order) or `-1` (programmatic focus)
- [ ] Live regions (`aria-live="polite"`) used for dynamic content updates (toasts, status changes)

## Testing (All React Versions)

- [ ] Tests query by role, label, or text — not by class name, ID, or test-ID as first choice
- [ ] `screen.getByText()` receives string or regex — not raw numbers (throws in RTL)
- [ ] `userEvent` preferred over `fireEvent` for simulating user interactions (more realistic)
- [ ] Async operations awaited with `waitFor()` or `findBy*` — not `getBy*` with manual delay
- [ ] Component tests render with required context providers (Redux, Router, i18n) via test wrapper
