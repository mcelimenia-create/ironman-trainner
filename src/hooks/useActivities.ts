import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';
import { useAuth } from '../lib/auth';
import { useRefresh } from '../lib/refreshContext';
import type { Activity } from '../types';

export function useActivities() {
  const { session } = useAuth();
  const { refreshKey } = useRefresh();
  const [activities, setActivities] = useState<Activity[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!session) return;
    setLoading(true);
    supabase
      .from('activities')
      .select('*')
      .eq('user_id', session.user.id)
      .order('date', { ascending: false })
      .limit(30)
      .then(({ data }) => {
        setActivities((data || []) as Activity[]);
        setLoading(false);
      });
  }, [session, refreshKey]);

  return { activities, loading };
}
