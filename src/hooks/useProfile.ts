import { useEffect, useState } from 'react';
import { supabase } from '../lib/supabase';
import { useAuth } from '../lib/auth';
import { useRefresh } from '../lib/refreshContext';

export interface Profile {
  id: string;
  name: string;
  race_date: string;
  race_type: string;
  level: string;
  ftp?: number;
  swim_css_sec?: number;
  run_threshold_pace_sec?: number;
  max_hr?: number;
  weight_kg?: number;
}

export function useProfile() {
  const { session } = useAuth();
  const { refreshKey } = useRefresh();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchProfile = async () => {
    if (!session) return;
    setLoading(true);
    const { data } = await supabase
      .from('profiles')
      .select('*')
      .eq('id', session.user.id)
      .single();
    if (data) setProfile(data as Profile);
    setLoading(false);
  };

  useEffect(() => { fetchProfile(); }, [session, refreshKey]);

  return { profile, loading, refetch: fetchProfile };
}
