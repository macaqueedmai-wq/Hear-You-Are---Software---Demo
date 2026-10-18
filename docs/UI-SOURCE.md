# 介面來源與改造記錄

日期：2026-10-13。Hear You Are 的前端由 React / TypeScript / Vite 提供，沒有使用 MoodHaven 的 Tauri、雲端同步或推論後端。

上游：[MoodHaven Journal](https://github.com/kenlacroix/moodhaven-journal)，實際讀取與改造的版本：`e63aeb879adea4e110c754732aa3c66314d07b95`。MIT 授權全文保存在 `third_party/moodhaven/LICENSE`，原始設計規範與兩個元件亦保存於該目錄。

直接重用或改造的部分：

- `src/components/layout/SidebarItem.tsx` → `web/src/components/SidebarItem.tsx`：保留圖示與短標籤的按鈕結構、`aria-current`、紫色選中狀態及鍵盤焦點模式；將 Tailwind 改為一般 CSS，移除桌面折疊功能，新增選中點。
- `tailwind.config.js` / `DESIGN.md` → `web/src/styles.css`：原樣沿用 `primary-50` 至 `primary-950` 的紫色色票，以 CSS 變數保存；改用系統字型。
- `src/components/ai/InsightCard.tsx` → `web/src/App.tsx` 的 DailyCard / CareCard：改造圓角卡片、標題與內容、來源依據、可關閉操作的元件模式；使用 lucide 圖示取代 emoji，接上此產品的真實 API 與條件原因。
- `DESIGN.md` 的白色圓角卡片、紫色 callout、減少動態效果規則 → 卡片與橫幅樣式。

新寫部分：錄音匯入、非虛構處理狀態、逐字稿來源與播放、記憶修正／版本衝突、人工主體確認、刪除確認、條件式關懷與偏好。本產品沒有沿用或宣稱 MoodHaven 的醫療能力。
