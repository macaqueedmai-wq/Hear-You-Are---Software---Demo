# UI source and adaptations

Original adaptation: **13 October 2026**. Hear You Are uses React, TypeScript and Vite. It does not use MoodHaven's Tauri application, cloud synchronization or inference backend.

The upstream project is [MoodHaven Journal](https://github.com/kenlacroix/moodhaven-journal), at commit [`e63aeb879adea4e110c754732aa3c66314d07b95`](https://github.com/kenlacroix/moodhaven-journal/tree/e63aeb879adea4e110c754732aa3c66314d07b95). Its complete MIT license is retained in [third_party/moodhaven/LICENSE](../third_party/moodhaven/LICENSE). The same directory preserves the original design guide and two component snapshots.

Directly reused or adapted elements:

- Upstream `src/components/layout/SidebarItem.tsx` became [`web/src/components/SidebarItem.tsx`](../web/src/components/SidebarItem.tsx). It retains the icon-and-label button structure, `aria-current`, violet selected state and keyboard focus treatment. Tailwind was replaced with regular CSS, desktop collapsing was removed and a selection indicator was added.
- Upstream `tailwind.config.js` and `DESIGN.md` informed [`web/src/styles.css`](../web/src/styles.css). The `primary-50` through `primary-950` violet palette is preserved as CSS variables; typography uses system fonts.
- Upstream `src/components/ai/InsightCard.tsx` informed the `DailyCard` and `CareCard` components in [`web/src/App.tsx`](../web/src/App.tsx): rounded cards, title/body structure, supporting evidence and dismiss actions. Lucide icons replace emoji, and the components connect to Hear You Are's APIs and condition explanations.
- `DESIGN.md` informed the white rounded cards, violet callouts and reduced-motion styling used in cards and banners.

Hear You Are adds recording import, actual processing status, transcript sources and playback, memory correction and version-conflict handling, explicit speaker confirmation, deletion confirmation, condition-based support and preferences. This project does not adopt or claim medical capabilities from MoodHaven.
