/** Adapted from MoodHaven Journal SidebarItem.tsx, MIT, © 2025 Ken LaCroix and contributors.
 * Original preserved at third_party/moodhaven/SidebarItem.original.tsx. */
import type { ReactNode } from "react";
interface SidebarItemProps {
  icon: ReactNode;
  label: string;
  isActive: boolean;
  onClick: () => void;
}
export function SidebarItem({
  icon,
  label,
  isActive,
  onClick,
}: SidebarItemProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`sidebar-item ${isActive ? "is-active" : ""}`}
      aria-current={isActive ? "page" : undefined}
    >
      <span className="sidebar-icon">{icon}</span>
      <span>{label}</span>
      {isActive && <span className="nav-active-dot" />}
    </button>
  );
}
