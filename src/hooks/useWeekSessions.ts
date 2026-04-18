import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';
import { useAuth } from '../lib/auth';
import { useRefresh } from '../lib/refreshContext';
import type { TrainingSession } from '../types';

export interface WeekGroup {
  weekStart: string;
  weekNumber: number;
  phase: string;
  sessions: TrainingSession[];
}

export function useWeekSessions() {
  const { session } = useAuth();
  const { refreshKey } = useRefresh();
  const [weeks, setWeeks] = useState<WeekGroup[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!session) return;
    supabase
      .from('training_sessions')
      .select('*')
      .eq('user_id', session.user.id)
      .order('date')
      .then(({ data }) => {
        if (!data) { setLoading(false); return; }
        const grouped = groupByWeek(data as TrainingSession[]);
        setWeeks(grouped);
        setLoading(false);
      });
  }, [session, refreshKey]);

  const updateSession = (id: string, completed: boolean) => {
    setWeeks(prev => prev.map(w => ({
      ...w,
      sessions: w.sessions.map(s => s.id === id ? { ...s, completed } : s),
    })));
  };

  return { weeks, loading, updateSession };
}

function groupByWeek(sessions: TrainingSession[]): WeekGroup[] {
  const map = new Map<string, TrainingSession[]>();
  for (const s of sessions) {
    const d = new Date(s.date + 'T12:00:00');
    const day = d.getDay();
    const monday = new Date(d);
    monday.setDate(d.getDate() - ((day + 6) % 7));
    const key = monday.toISOString().split('T')[0];
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(s);
  }

  const planStart = sessions[0] ? new Date(sessions[0].date) : new Date();

  return Array.from(map.entries()).map(([weekStart, wSessions], i) => {
    const weekNum = i + 1;
    const totalWeeks = map.size;
    let phase = 'Base';
    if (weekNum > totalWeeks * 0.75) phase = 'Taper';
    else if (weekNum > totalWeeks * 0.60) phase = 'Peak';
    else if (weekNum > totalWeeks * 0.45) phase = 'Build';
    return { weekStart, weekNumber: weekNum, phase, sessions: wSessions };
  });
}
