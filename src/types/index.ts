export type Discipline = 'swim' | 'bike' | 'run' | 'gym' | 'rest' | 'brick';
export type ExerciseCategory = 'core' | 'legs' | 'upper' | 'hip' | 'mobility';

export interface Exercise {
  name: string;
  sets: number;
  reps: string;
  rest_sec: number;
  category: ExerciseCategory;
  notes?: string;
}

export interface GymBlock {
  title: string;
  exercises: Exercise[];
}

export interface UserProfile {
  id: string;
  name: string;
  email: string;
  race_date: string;
  ftp: number;
  swim_css: number;
  run_threshold_pace: number;
  max_hr: number;
  weight_kg: number;
  race_type: 'sprint' | 'olympic' | 'half_ironman' | 'full_ironman';
  level: 'beginner' | 'intermediate' | 'advanced';
  strava_connected: boolean;
}

export interface TrainingSession {
  id: string;
  user_id: string;
  date: string;
  discipline: Discipline;
  title: string;
  description: string;
  duration_min: number;
  distance_km?: number;
  tss?: number;
  completed: boolean;
  activity_id?: string;
}

export interface Activity {
  id: string;
  user_id: string;
  strava_id?: string;
  discipline: Discipline;
  title: string;
  date: string;
  duration_min: number;
  distance_km: number;
  avg_hr?: number;
  max_hr?: number;
  avg_speed?: number;
  elevation_gain?: number;
  tss?: number;
}

export interface WeekSummary {
  week_start: string;
  swim_km: number;
  bike_km: number;
  run_km: number;
  total_hours: number;
  tss_total: number;
  sessions_completed: number;
  sessions_planned: number;
}
