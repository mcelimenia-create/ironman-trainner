import type { TrainingSession, Discipline } from '../types';

interface PlanConfig {
  userId: string;
  raceDate: string;
  level: 'beginner' | 'intermediate' | 'advanced';
  raceType?: 'sprint' | 'olympic' | 'half_ironman' | 'full_ironman';
}

interface SessionTemplate {
  dayOffset: number;
  discipline: Discipline;
  title: string;
  description: string;
  duration_min: number;
  distance_km?: number;
  tss: number;
}

interface WeekTemplate {
  phase: string;
  sessions: SessionTemplate[];
}

// Multipliers applied to full ironman volumes per race type
const RACE_MULTIPLIERS: Record<string, { volume: number; intensity: number }> = {
  sprint:       { volume: 0.30, intensity: 1.4 },
  olympic:      { volume: 0.50, intensity: 1.2 },
  half_ironman: { volume: 0.65, intensity: 1.1 },
  full_ironman: { volume: 1.00, intensity: 1.0 },
};

const RACE_DAY: Record<string, SessionTemplate> = {
  sprint: {
    dayOffset: 6, discipline: 'brick', title: 'SPRINT RACE DAY ⚡',
    description: '750m natación + 20km bici + 5km carrera. Ve a tope desde el inicio.',
    duration_min: 70, distance_km: 25.75, tss: 120,
  },
  olympic: {
    dayOffset: 6, discipline: 'brick', title: 'TRIATLÓN OLÍMPICO RACE DAY 🏅',
    description: '1.5km natación + 40km bici + 10km carrera. Ritmo alto sostenido.',
    duration_min: 130, distance_km: 51.5, tss: 180,
  },
  half_ironman: {
    dayOffset: 6, discipline: 'brick', title: 'IRONMAN 70.3 RACE DAY 🔶',
    description: '1.9km natación + 90km bici + 21.1km carrera. Gestiona el esfuerzo y disfrútalo.',
    duration_min: 270, distance_km: 113, tss: 250,
  },
  full_ironman: {
    dayOffset: 6, discipline: 'run', title: 'IRONMAN RACE DAY 🔴',
    description: '3.8km natación + 180km bici + 42.2km carrera. Disfrútalo.',
    duration_min: 660, distance_km: 226, tss: 350,
  },
};

// Minimum weeks recommended per race type
const MIN_WEEKS: Record<string, number> = {
  sprint: 4, olympic: 6, half_ironman: 10, full_ironman: 16,
};

const BASE_TEMPLATES: WeekTemplate[] = [
  {
    phase: 'base',
    sessions: [
      { dayOffset: 0, discipline: 'swim', title: 'Técnica natación', description: 'Trabajo de técnica: brazada, posición horizontal, patada. Usa pull buoy si es necesario.', duration_min: 50, distance_km: 2.0, tss: 40 },
      { dayOffset: 1, discipline: 'run', title: 'Carrera suave Z2', description: 'Rodaje conversacional. FC entre 120-140 bpm. Zona 2 estricta.', duration_min: 55, distance_km: 9, tss: 50 },
      { dayOffset: 2, discipline: 'gym', title: 'Fuerza Base', description: 'Sesión de fuerza base para triatlón.', duration_min: 60, tss: 45 },
      { dayOffset: 3, discipline: 'bike', title: 'Rodaje aeróbico Z2', description: 'Pedaleo suave en cadencia alta (90-95 rpm). Zona 2 estricta.', duration_min: 75, distance_km: 40, tss: 65 },
      { dayOffset: 4, discipline: 'swim', title: 'Natación continua', description: 'Nado continuo a ritmo cómodo. Foco en eficiencia.', duration_min: 45, distance_km: 2.2, tss: 38 },
      { dayOffset: 5, discipline: 'bike', title: 'Salida larga Z2', description: 'Salida larga aeróbica. Come y bebe cada 20 min.', duration_min: 150, distance_km: 80, tss: 110 },
      { dayOffset: 6, discipline: 'run', title: 'Rodaje largo Z2', description: 'Rodaje largo aeróbico. Los últimos 15min a ritmo de carrera.', duration_min: 80, distance_km: 13, tss: 72 },
    ],
  },
  {
    phase: 'build',
    sessions: [
      { dayOffset: 0, discipline: 'swim', title: 'Series umbral natación', description: 'Series de 400m a ritmo CSS. Pausa 30seg entre series.', duration_min: 60, distance_km: 3.0, tss: 65 },
      { dayOffset: 1, discipline: 'run', title: 'Tempo Z3-Z4', description: '2x15min a ritmo umbral. Recuperación 5min trotando.', duration_min: 65, distance_km: 11, tss: 72 },
      { dayOffset: 2, discipline: 'gym', title: 'Fuerza Neuromuscular', description: 'Potencia y fuerza máxima para pico de rendimiento.', duration_min: 55, tss: 50 },
      { dayOffset: 3, discipline: 'brick', title: 'Brick bici + carrera', description: 'Bici Z2-Z3 seguido inmediatamente de carrera a ritmo objetivo. Practica la transición.', duration_min: 100, distance_km: 50, tss: 95 },
      { dayOffset: 4, discipline: 'swim', title: 'Natación progresiva', description: 'Series progresivas acelerando de CSS+10 a CSS. Descanso 20seg.', duration_min: 55, distance_km: 3.0, tss: 60 },
      { dayOffset: 5, discipline: 'bike', title: 'Fondo con intensidad', description: 'Fondo largo con los últimos 40min a ritmo objetivo de carrera.', duration_min: 180, distance_km: 95, tss: 140 },
      { dayOffset: 6, discipline: 'run', title: 'Rodaje largo + ritmo', description: 'Rodaje largo con el tramo central a ritmo objetivo de carrera.', duration_min: 95, distance_km: 16, tss: 88 },
    ],
  },
  {
    phase: 'peak',
    sessions: [
      { dayOffset: 0, discipline: 'swim', title: 'Series intensivas', description: 'Series cortas a máxima velocidad sostenible. Simula el start de carrera.', duration_min: 60, distance_km: 3.5, tss: 70 },
      { dayOffset: 1, discipline: 'run', title: 'Intervals VO2max', description: '5x1km a ritmo 5K. Recuperación 2min trotando. Esfuerzo máximo sostenible.', duration_min: 60, distance_km: 11, tss: 82 },
      { dayOffset: 2, discipline: 'gym', title: 'Movilidad y Prevención', description: 'Sesión de movilidad articular y trabajo preventivo.', duration_min: 45, tss: 25 },
      { dayOffset: 3, discipline: 'brick', title: 'Simulacro parcial', description: 'Bici larga a ritmo objetivo + carrera inmediata a ritmo de carrera. Prueba toda la nutrición.', duration_min: 240, distance_km: 130, tss: 185 },
      { dayOffset: 4, discipline: 'swim', title: 'Natación aguas abiertas', description: 'Nada en lago o mar si es posible. Practica orientación y drafting.', duration_min: 60, distance_km: 3.5, tss: 65 },
      { dayOffset: 5, discipline: 'bike', title: 'Fondo pico', description: 'Salida larga a ritmo objetivo. Momento de mayor volumen del plan.', duration_min: 240, distance_km: 130, tss: 185 },
      { dayOffset: 6, discipline: 'run', title: 'Carrera larga pico', description: 'Carrera larga a ritmo objetivo. Practica nutrición: gel cada 45min.', duration_min: 110, distance_km: 20, tss: 102 },
    ],
  },
  {
    phase: 'taper',
    sessions: [
      { dayOffset: 0, discipline: 'swim', title: 'Natación activación', description: 'Nado suave con aceleraciones cortas. Sin acumular fatiga.', duration_min: 35, distance_km: 1.8, tss: 28 },
      { dayOffset: 1, discipline: 'run', title: 'Carrera ligera', description: 'Rodaje suave con 4x200m a ritmo carrera para mantener las piernas.', duration_min: 35, distance_km: 6, tss: 28 },
      { dayOffset: 2, discipline: 'rest', title: 'Descanso activo', description: 'Paseo de 30min + estiramientos. Sin entrenar.', duration_min: 30, tss: 10 },
      { dayOffset: 3, discipline: 'bike', title: 'Rodillo activación', description: '45min Z1-Z2 con 3x5min a ritmo objetivo. Solo activar, no fatigar.', duration_min: 45, distance_km: 25, tss: 35 },
      { dayOffset: 4, discipline: 'swim', title: 'Reconocimiento agua', description: 'Si es posible, nada en el lugar de la carrera. Solo 20min suave.', duration_min: 20, distance_km: 1.0, tss: 15 },
      { dayOffset: 5, discipline: 'rest', title: 'Descanso total', description: 'Paseo suave, estiramientos, prepara el material.', duration_min: 20, tss: 5 },
    ],
  },
];

export function generatePlan(config: PlanConfig): Omit<TrainingSession, 'id'>[] {
  const { userId, raceDate, level, raceType = 'full_ironman' } = config;

  const race = new Date(raceDate + 'T12:00:00');
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const totalDays = Math.floor((race.getTime() - today.getTime()) / (1000 * 60 * 60 * 24));
  const minWeeks = MIN_WEEKS[raceType] || 4;
  const totalWeeks = Math.max(Math.floor(totalDays / 7), minWeeks);

  const multiplier = RACE_MULTIPLIERS[raceType] || RACE_MULTIPLIERS.full_ironman;
  const levelFactor = level === 'beginner' ? 0.75 : level === 'advanced' ? 1.2 : 1.0;
  const volFactor = multiplier.volume * levelFactor;

  const phaseWeeks = {
    base:  Math.max(Math.floor(totalWeeks * 0.45), 2),
    build: Math.max(Math.floor(totalWeeks * 0.30), 1),
    peak:  Math.max(Math.floor(totalWeeks * 0.15), 1),
    taper: Math.max(Math.floor(totalWeeks * 0.10), 1),
  };

  const sessions: Omit<TrainingSession, 'id'>[] = [];
  let weekIndex = 0;

  const phases: { phase: string; weeks: number }[] = [
    { phase: 'base',  weeks: phaseWeeks.base },
    { phase: 'build', weeks: phaseWeeks.build },
    { phase: 'peak',  weeks: phaseWeeks.peak },
    { phase: 'taper', weeks: phaseWeeks.taper },
  ];

  for (const { phase, weeks } of phases) {
    const template = BASE_TEMPLATES.find(t => t.phase === phase) || BASE_TEMPLATES[0];
    const isTaper = phase === 'taper';

    for (let w = 0; w < weeks; w++) {
      const isRecoveryWeek = !isTaper && (w + 1) % 4 === 0;
      const weekFactor = isRecoveryWeek ? 0.7 : 1.0;
      const isLastTaperWeek = isTaper && w === weeks - 1;

      for (const s of template.sessions) {
        const sessionDate = new Date(today);
        sessionDate.setDate(today.getDate() + weekIndex * 7 + s.dayOffset);
        if (sessionDate >= race) continue;

        const dur = Math.max(Math.round(s.duration_min * volFactor * weekFactor), 20);
        const dist = s.distance_km ? parseFloat((s.distance_km * volFactor * weekFactor).toFixed(1)) : undefined;
        const tss = Math.max(Math.round(s.tss * volFactor * weekFactor), 5);

        sessions.push({
          user_id: userId,
          date: sessionDate.toISOString().split('T')[0],
          discipline: s.discipline,
          title: isRecoveryWeek ? `[Recuperación] ${s.title}` : s.title,
          description: s.description,
          duration_min: dur,
          distance_km: dist,
          tss,
          completed: false,
        });
      }

      // Add race day on the last week of taper
      if (isLastTaperWeek) {
        const raceDay = RACE_DAY[raceType];
        const raceDayDate = new Date(raceDate + 'T12:00:00');
        sessions.push({
          user_id: userId,
          date: raceDayDate.toISOString().split('T')[0],
          discipline: raceDay.discipline,
          title: raceDay.title,
          description: raceDay.description,
          duration_min: raceDay.duration_min,
          distance_km: raceDay.distance_km,
          tss: raceDay.tss,
          completed: false,
        });
      }

      weekIndex++;
    }
  }

  return sessions;
}
