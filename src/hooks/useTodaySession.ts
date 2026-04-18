import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';
import { useAuth } from '../lib/auth';
import { useRefresh } from '../lib/refreshContext';
import type { TrainingSession } from '../types';

export function useTodaySession() {
  const { session } = useAuth();
  const { refreshKey } = useRefresh();
  const [todaySession, setTodaySession] = useState<TrainingSession | null>(null);
  const [loading, setLoading] = useState(true);

  const today = new Date().toISOString().split('T')[0];

  const fetch = async () => {
    if (!session) return;
    setLoading(true);
    const { data } = await supabase
      .from('training_sessions')
      .select('*')
      .eq('user_id', session.user.id)
      .eq('date', today)
      .order('discipline')
      .limit(1)
      .single();
    setTodaySession(data as TrainingSession | null);
    setLoading(false);
  };

  const markCompleted = async (id: string) => {
    await supabase.from('training_sessions').update({ completed: true }).eq('id', id);
    setTodaySession(s => s ? { ...s, completed: true } : s);
  };

  useEffect(() => { fetch(); }, [session, refreshKey]);

  return { todaySession, loading, markCompleted };
}
