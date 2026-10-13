/**
 * InsightCard - Display a wellness insight
 */

import type { WellnessInsight } from '../../types/ai';

interface InsightCardProps {
  insight: WellnessInsight;
  onDismiss: (id: string) => void;
}

const TYPE_ICONS: Record<WellnessInsight['type'], string> = {
  observation: '👁️',
  suggestion: '💡',
  celebration: '🎉',
  pattern: '📊',
};

const TYPE_COLORS: Record<WellnessInsight['type'], string> = {
  observation: 'bg-slate-50 dark:bg-slate-800/50 border-slate-200 dark:border-slate-700',
  suggestion: 'bg-violet-50 dark:bg-violet-900/20 border-violet-200 dark:border-violet-800',
  celebration: 'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-200 dark:border-emerald-800',
  pattern: 'bg-sky-50 dark:bg-sky-900/20 border-sky-200 dark:border-sky-800',
};

const PRIORITY_INDICATORS: Record<WellnessInsight['priority'], string> = {
  low: '',
  medium: 'ring-2 ring-amber-300 dark:ring-amber-600',
  high: 'ring-2 ring-violet-400 dark:ring-violet-500',
};

export function InsightCard({ insight, onDismiss }: InsightCardProps) {
  return (
    <div
      className={`
        relative p-4 rounded-xl border transition-all duration-200
        ${TYPE_COLORS[insight.type]}
        ${PRIORITY_INDICATORS[insight.priority]}
      `}
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex items-center gap-2">
          <span className="text-lg">{TYPE_ICONS[insight.type]}</span>
          <h4 className="font-semibold text-slate-700 dark:text-slate-200">
            {insight.title}
          </h4>
        </div>

        <button
          type="button"
          onClick={() => onDismiss(insight.id)}
          className="p-1 rounded-full text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 hover:bg-white/50 dark:hover:bg-black/20 transition-colors"
          aria-label="Dismiss insight"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Message */}
      <p className="text-slate-600 dark:text-slate-300 mb-2">
        {insight.message}
      </p>

      {/* Based on */}
      <p className="text-xs text-slate-500 dark:text-slate-400">
        Based on: {insight.basedOn}
      </p>
    </div>
  );
}
