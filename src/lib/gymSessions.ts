import type { GymBlock } from '../types';

export const GYM_SESSIONS: Record<string, { title: string; tss: number; duration_min: number; blocks: GymBlock[] }> = {
  fuerza_base: {
    title: 'Fuerza Base — Triatlón',
    tss: 45,
    duration_min: 60,
    blocks: [
      {
        title: '🔥 Activación (10 min)',
        exercises: [
          { name: 'Glute bridge', sets: 2, reps: '15', rest_sec: 30, category: 'hip', notes: 'Aprieta glúteos arriba 2 segundos' },
          { name: 'Clamshell con banda', sets: 2, reps: '15 por lado', rest_sec: 30, category: 'hip' },
          { name: 'Dead bug', sets: 2, reps: '8 por lado', rest_sec: 30, category: 'core', notes: 'Zona lumbar pegada al suelo siempre' },
        ],
      },
      {
        title: '💪 Bloque principal (35 min)',
        exercises: [
          { name: 'Sentadilla goblet', sets: 3, reps: '10', rest_sec: 90, category: 'legs', notes: 'Talones en el suelo, rodillas hacia fuera' },
          { name: 'Peso muerto rumano', sets: 3, reps: '10', rest_sec: 90, category: 'legs', notes: 'Espalda recta, bisagra de cadera' },
          { name: 'Zancada búlgara', sets: 3, reps: '8 por pierna', rest_sec: 90, category: 'legs', notes: 'Control de la bajada (3 seg)' },
          { name: 'Remo con mancuerna', sets: 3, reps: '10 por lado', rest_sec: 60, category: 'upper', notes: 'Codo cerca del cuerpo' },
          { name: 'Press de hombro', sets: 3, reps: '10', rest_sec: 60, category: 'upper' },
        ],
      },
      {
        title: '🧘 Core finisher (15 min)',
        exercises: [
          { name: 'Plank frontal', sets: 3, reps: '45 seg', rest_sec: 30, category: 'core', notes: 'Cuerpo recto, no subas las caderas' },
          { name: 'Plank lateral', sets: 2, reps: '30 seg/lado', rest_sec: 30, category: 'core' },
          { name: 'Pallof press', sets: 3, reps: '10 por lado', rest_sec: 30, category: 'core', notes: 'Resistir la rotación, no el movimiento' },
          { name: 'Hip thrust con barra', sets: 3, reps: '12', rest_sec: 60, category: 'hip', notes: 'Toca el suelo con glúteos entre reps' },
        ],
      },
    ],
  },
  fuerza_neuromuscular: {
    title: 'Fuerza Neuromuscular',
    tss: 50,
    duration_min: 55,
    blocks: [
      {
        title: '⚡ Potencia (20 min)',
        exercises: [
          { name: 'Salto al cajón', sets: 4, reps: '5', rest_sec: 90, category: 'legs', notes: 'Aterriza suave, amortiguando con rodillas' },
          { name: 'Salto vertical con contramovimiento', sets: 4, reps: '5', rest_sec: 90, category: 'legs' },
          { name: 'Sprints en rampa (escalón)', sets: 6, reps: '6 seg', rest_sec: 60, category: 'legs' },
        ],
      },
      {
        title: '🏋️ Fuerza máxima (25 min)',
        exercises: [
          { name: 'Sentadilla con barra', sets: 4, reps: '5', rest_sec: 120, category: 'legs', notes: 'Carga al 75-80% del máximo' },
          { name: 'Peso muerto', sets: 3, reps: '4', rest_sec: 120, category: 'legs', notes: 'Carga progresiva — sin redondear espalda' },
          { name: 'Dominadas', sets: 3, reps: 'Al fallo', rest_sec: 90, category: 'upper' },
        ],
      },
      {
        title: '🧘 Core antirotación (10 min)',
        exercises: [
          { name: 'Bird dog', sets: 3, reps: '6 por lado', rest_sec: 30, category: 'core', notes: 'Movimiento lento y controlado' },
          { name: 'Rueda abdominal', sets: 3, reps: '8', rest_sec: 45, category: 'core' },
        ],
      },
    ],
  },
  movilidad_prevencion: {
    title: 'Movilidad y Prevención',
    tss: 25,
    duration_min: 45,
    blocks: [
      {
        title: '🦵 Cadera y tobillos (15 min)',
        exercises: [
          { name: 'Círculos de cadera', sets: 2, reps: '10 por lado', rest_sec: 0, category: 'mobility' },
          { name: 'Hip flexor stretch 90/90', sets: 2, reps: '60 seg/lado', rest_sec: 0, category: 'mobility' },
          { name: 'Movilidad tobillo en pared', sets: 2, reps: '15 por lado', rest_sec: 0, category: 'mobility' },
          { name: 'Piriforme (figura 4)', sets: 2, reps: '60 seg/lado', rest_sec: 0, category: 'mobility' },
        ],
      },
      {
        title: '💪 Hombros y torácica (15 min)',
        exercises: [
          { name: 'Apertura torácica con foam roller', sets: 2, reps: '10 reps', rest_sec: 0, category: 'mobility' },
          { name: 'Rotación externa hombro (banda)', sets: 3, reps: '15 por lado', rest_sec: 30, category: 'upper', notes: 'Esencial para natación' },
          { name: 'Face pull', sets: 3, reps: '15', rest_sec: 30, category: 'upper' },
        ],
      },
      {
        title: '🏃 Específico carrera (15 min)',
        exercises: [
          { name: 'Elevaciones de rodilla (marcha)', sets: 2, reps: '30 m', rest_sec: 30, category: 'hip' },
          { name: 'Skipping alto', sets: 3, reps: '20 m', rest_sec: 30, category: 'legs' },
          { name: 'Zancada con rotación', sets: 2, reps: '10 por lado', rest_sec: 30, category: 'mobility' },
        ],
      },
    ],
  },
};
