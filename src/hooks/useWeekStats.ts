import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';
import { useAuth } from '../lib/auth';
import { useRefresh } from '../lib/refreshContext';

export interface WeekStats {
  swimKm: number;
  bikeKm: number;
  runKm: number;
  totalHours: number;
  sessionsCompleted: number;
  sessionsTotal: number;
  tssTotal: number;
}

export function useWeekStats() {
  const { session } = useAuth();
  const { refreshKey } = useRefresh();
  const [stats, setStats] = useState<WeekStats | null>(null);

  useEffect(() => {
    if (!session) return;
    const now = new Date();
    const day = now.getDay();
    const monday = new Date(now);
    monday.setDate(now.getDate() - ((day + 6) % 7));
    monday.setHours(0, 0, 0, 0);
    const sunday = new Date(monday);
    sunday.setDate(monday.getDate() + 6);
    sunday.setHours(23, 59, 59, 999);

    supabase
      .from('training_sessions')
      .select('discipline, distance_km, duration_min, tss, completed')
      .eq('user_id', session.user.id)
      .gte('date', monday.toISOString().split('T')[0])
      .lte('date', sunday.toISOString().split('T')[0])
      .then(({ data }) => {
        if (!data) return;
        const s: WeekStats = { swimKm: 0, bikeKm: 0, runKm: 0, totalHours: 0, sessionsCompleted: 0, sessionsTotal: data.length, tssTotal: 0 };
        for (const row of data) {
          const km = row.distance_km || 0;
          if (row.discipline === 'swim') s.swimKm += km;
          else if (row.discipline === 'bike' || row.discipline === 'brick') s.bikeKm += km;
          else if (row.discipline === 'run') s.runKm += km;
          s.totalHours += (row.duration_min || 0) / 60;
          if (row.completed) { s.sessionsCompleted++; s.tssTotal += row.tss || 0; }
        }
        s.swimKm = Math.round(s.swimKm * 10) / 10;
        s.bikeKm = Math.round(s.bikeKm);
        s.runKm = Math.round(s.runKm * 10) / 10;
        s.totalHours = Math.round(s.totalHours * 10) / 10;
        setStats(s);
      });
  }, [session, refreshKey]);

  return stats;
}
