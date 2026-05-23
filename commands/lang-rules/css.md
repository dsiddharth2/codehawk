# CSS / SCSS Review Rules

## All Versions

### Layout & Box Model
- [ ] No `!important` unless overriding third-party styles — document reason in a comment
- [ ] `box-sizing: border-box` applied globally or per component — not mixed models
- [ ] `z-index` values use a defined scale (10, 20, 30...) — no arbitrary large numbers (99999)
- [ ] Flexbox or Grid used for layout — no `float`-based layouts in new code
- [ ] No negative margins for layout — use gap, padding, or flexbox alignment
- [ ] Fixed dimensions (`width: 300px`) not used on containers that hold dynamic content — use `min-width`/`max-width`
- [ ] `overflow: hidden` not used to hide layout bugs — fix the underlying overflow cause

### Naming & Organization
- [ ] Class names use project convention (camelCase for CSS Modules, BEM for global CSS) — consistent within file
- [ ] No element selectors (`div`, `span`, `p`) for styling — use class selectors for specificity control
- [ ] No ID selectors (`#header`) for styling — IDs are for anchors and JS hooks only
- [ ] Selectors max 3 levels deep — avoid `.parent .child .grandchild .great-grandchild` specificity chains
- [ ] No inline styles in markup for static styling — use classes; inline only for truly dynamic values (JS-computed)
- [ ] Color values use project design tokens / CSS custom properties — not hardcoded hex/rgb in component styles
- [ ] Repeated values (colors, spacing, fonts, shadows) extracted to CSS custom properties or SCSS variables

### Typography & Spacing
- [ ] Font sizes use relative units (`rem`, `em`) or project scale — not arbitrary `px` values
- [ ] Line height uses unitless values (`1.5`) — not pixel values that break on font-size changes
- [ ] Spacing uses consistent scale (multiples of 4px or 8px) — not arbitrary values
- [ ] `text-overflow: ellipsis` paired with `overflow: hidden` and `white-space: nowrap` (all three required)

### Responsive & Cross-Browser
- [ ] Media queries use the project's defined breakpoints — no arbitrary pixel values
- [ ] Mobile-first (`min-width`) or desktop-first (`max-width`) approach consistent within project — not mixed
- [ ] No vendor prefixes in source CSS when autoprefixer is configured — let the tool handle it
- [ ] `touch-action` set on interactive elements for mobile — prevents 300ms tap delay
- [ ] Minimum touch target size 44x44px on interactive elements

### Performance
- [ ] No `*` universal selector in component styles — acceptable only in reset/normalize
- [ ] `will-change` used sparingly and only on elements that actually animate — not as a blanket optimization
- [ ] Animations use `transform` and `opacity` — not `top`/`left`/`width`/`height` (triggers layout)
- [ ] Transitions under 300ms for UI feedback; `prefers-reduced-motion` media query respected
- [ ] Large background images use `image-set()` or responsive `srcset` — not desktop-size images on mobile
- [ ] `contain: layout` or `contain: content` used on independently-rendered components to limit paint scope

### Accessibility
- [ ] Color contrast meets WCAG AA (4.5:1 for text, 3:1 for large text / UI components)
- [ ] Focus styles visible and not removed (`outline: none` only when replaced with equivalent visible indicator)
- [ ] `:focus-visible` used instead of `:focus` to avoid focus rings on mouse click
- [ ] Content not hidden with `display: none` when it should be screen-reader accessible — use `.sr-only` pattern
- [ ] No `user-select: none` on text content users might need to copy
- [ ] Decorative elements use `aria-hidden="true"` — no alt text on decorative images

### Common Bugs
- [ ] `position: sticky` has a scrollable ancestor — fails silently if any ancestor has `overflow: hidden`
- [ ] `gap` property supported by target browsers when used with flexbox (not supported in older Safari)
- [ ] Percentage heights have an explicit height on the parent — percentage height on `auto`-height parent is 0
- [ ] `calc()` expressions have spaces around operators — `calc(100%-20px)` silently fails
- [ ] `transition: all` not used — specify exact properties to avoid animating unintended changes

## CSS Modules

- [ ] File naming follows convention: `ComponentName.module.css` (or `.module.scss`)
- [ ] Import as `styles` object: `import styles from './Component.module.css'`
- [ ] Class access uses dot notation (`styles.container`) — not bracket notation (`styles['container']`)
- [ ] `:global(.class)` used sparingly — only for third-party overrides, documented with comment
- [ ] `composes` used for style composition — not `@extend` or class concatenation in markup

## SCSS

- [ ] Nesting max 3 levels deep — deeper nesting produces overly specific selectors
- [ ] `@use` and `@forward` used instead of `@import` (deprecated in Dart Sass)
- [ ] Mixins used for repeated patterns with parameters — not for single-use style blocks
- [ ] `@each` / `@for` loops used for generating utility classes — not copy-pasted variants
- [ ] Placeholder selectors (`%placeholder`) used with `@extend` — not class-based `@extend` (avoids unintended selector bloat)
- [ ] Variables use meaningful names (`$color-primary`, `$spacing-md`) — not generic (`$a`, `$temp`)
- [ ] Maps used for structured data (breakpoints, colors, z-index scale) — accessed with `map.get()`
- [ ] Partials prefixed with underscore (`_variables.scss`) and loaded via `@use`
