import React from 'react';
import { View, Text, ScrollView, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors } from '../lib/theme';
import { useTodaySession } from '../hooks/useTodaySession';
import { useProfile } from '../hooks/useProfile';
import { useWeekStats } from '../hooks/useWeekStats';
import GymSessionView from '../components/GymSessionView';
import { GYM_SESSIONS } from '../lib/gymSessions';

const DISCIPLINE_CONFIG = {
  swim:  { icon: 'water',    color: '#3B82F6', label: 'Natación' },
  bike:  { icon: 'bicycle',  color: '#F59E0B', label: 'Bici' },
  run:   { icon: 'walk',     color: '#EF4444', label: 'Carrera' },
  gym:   { icon: 'barbell',  color: '#8B5CF6', label: 'Fuerza' },
  brick: { icon: 'flash',    color: '#F97316', label: 'Brick' },
  rest:  { icon: 'moon',     color: '#6B7280', label: 'Descanso' },
} as const;

const GYM_SESSION_MAP: Record<string, string> = {
  'Fuerza Base': 'fuerza_base',
  'Fuerza Neuromuscular': 'fuerza_neuromuscular',
  'Movilidad': 'movilidad_prevencion',
};

export default function TodayScreen() {
  const { todaySession, loading: sessionLoading, markCompleted } = useTodaySession();
  const { profile, loading: profileLoading } = useProfile();
  const weekStats = useWeekStats();

  const loading = sessionLoading || profileLoading;

  const daysToRace = profile?.race_date
    ? Math.max(0, Math.floor((new Date(profile.race_date).getTime() - Date.now()) / (1000 * 60 * 60 * 24)))
    : null;

  const today = new Date();
  const dateStr = today.toLocaleDateString('es-ES', { weekday: 'long', day: 'numeric', month: 'long' });

  const firstName = profile?.name?.split(' ')[0] || 'atleta';

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  const session = todaySession;
  const config = session ? DISCIPLINE_CONFIG[session.discipline as keyof typeof DISCIPLINE_CONFIG] : null;
  const gymKey = session?.discipline === 'gym'
    ? Object.entries(GYM_SESSION_MAP).find(([k]) => session.title.includes(k))?.[1] ?? 'fuerza_base'
    : null;

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <View style={styles.header}>
        <Text style={styles.greeting}>Buenos días, {firstName}</Text>
        <Text style={styles.date}>{dateStr}</Text>
      </View>

      {/* Cuenta atrás */}
      {daysToRace !== null && (
        <View style={styles.raceCard}>
          <Text style={styles.raceLabel}>
            {profile?.race_type === 'olympic' ? 'TRIATLÓN OLÍMPICO' :
             profile?.race_type === 'half_ironman' ? 'IRONMAN 70.3' : 'IRONMAN'}
          </Text>
          <Text style={styles.raceDays}>{daysToRace}</Text>
          <Text style={styles.raceDaysLabel}>días para la carrera</Text>
          <Text style={styles.raceDate}>
            {new Date(profile!.race_date).toLocaleDateString('es-ES', { day: 'numeric', month: 'long', year: 'numeric' })}
          </Text>
        </View>
      )}

      {/* Sesión de hoy */}
      <Text style={styles.sectionTitle}>Entrenamiento de hoy</Text>

      {!session ? (
        <View style={styles.restCard}>
          <Text style={styles.restEmoji}>🌙</Text>
          <Text style={styles.restTitle}>Día de descanso</Text>
          <Text style={styles.restDesc}>Recupera bien. El descanso es parte del entrenamiento.</Text>
        </View>
      ) : (
        <View style={[styles.sessionCard, { borderLeftColor: config!.color }]}>
          <View style={styles.sessionHeader}>
            <View style={[styles.disciplineBadge, { backgroundColor: config!.color + '20' }]}>
              <Ionicons name={config!.icon as any} size={20} color={config!.color} />
              <Text style={[styles.disciplineLabel, { color: config!.color }]}>{config!.label}</Text>
            </View>
            {session.tss && (
              <View style={styles.tssChip}>
                <Text style={styles.tssText}>TSS {session.tss}</Text>
              </View>
            )}
          </View>

          <Text style={styles.sessionTitle}>{session.title}</Text>
          <Text style={styles.sessionDesc}>{session.description}</Text>

          <View style={styles.sessionStats}>
            <View style={styles.stat}>
              <Ionicons name="time-outline" size={16} color={colors.textSecondary} />
              <Text style={styles.statText}>{session.duration_min} min</Text>
            </View>
            {session.distance_km && (
              <View style={styles.stat}>
                <Ionicons name="navigate-outline" size={16} color={colors.textSecondary} />
                <Text style={styles.statText}>{session.distance_km} km</Text>
              </View>
            )}
          </View>

          {gymKey && (
            <View style={styles.gymSection}>
              <GymSessionView blocks={GYM_SESSIONS[gymKey].blocks} />
            </View>
          )}

          {!session.completed ? (
            <TouchableOpacity
              style={[styles.doneButton, { backgroundColor: config!.color }]}
              onPress={() => markCompleted(session.id)}
            >
              <Ionicons name="checkmark-circle-outline" size={20} color="#fff" />
              <Text style={styles.doneButtonText}>Marcar como completado</Text>
            </TouchableOpacity>
          ) : (
            <View style={styles.completedBadge}>
              <Ionicons name="checkmark-circle" size={20} color="#22C55E" />
              <Text style={styles.completedText}>Completado</Text>
            </View>
          )}
        </View>
      )}

      {/* Resumen semana */}
      <Text style={styles.sectionTitle}>Esta semana</Text>
      <View style={styles.weekRow}>
        <WeekStat icon="water"   color="#3B82F6" value={weekStats ? `${weekStats.swimKm}` : '—'} unit="km" label="Swim" />
        <WeekStat icon="bicycle" color="#F59E0B" value={weekStats ? `${weekStats.bikeKm}` : '—'} unit="km" label="Bike" />
        <WeekStat icon="walk"    color="#EF4444" value={weekStats ? `${weekStats.runKm}` : '—'}  unit="km" label="Run" />
        <WeekStat icon="time"    color="#6B7280" value={weekStats ? `${weekStats.totalHours}` : '—'} unit="h" label="Total" />
      </View>
    </ScrollView>
  );
}

function WeekStat({ icon, color, value, unit, label }: any) {
  return (
    <View style={styles.weekStat}>
      <Ionicons name={icon} size={18} color={color} />
      <Text style={styles.weekStatValue}>
        {value}<Text style={styles.weekStatUnit}>{unit}</Text>
      </Text>
      <Text style={styles.weekStatLabel}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: 20, paddingBottom: 40 },
  centered: { flex: 1, backgroundColor: colors.background, alignItems: 'center', justifyContent: 'center' },
  header: { marginBottom: 20 },
  greeting: { fontSize: 26, fontWeight: '700', color: colors.text },
  date: { fontSize: 14, color: colors.textSecondary, marginTop: 2, textTransform: 'capitalize' },
  raceCard: {
    backgroundColor: '#1C1C1E', borderRadius: 16, padding: 20,
    alignItems: 'center', marginBottom: 28, borderWidth: 1, borderColor: '#EF4444',
  },
  raceLabel: { fontSize: 12, fontWeight: '700', color: '#EF4444', letterSpacing: 2 },
  raceDays: { fontSize: 56, fontWeight: '800', color: colors.text, lineHeight: 64 },
  raceDaysLabel: { fontSize: 14, color: colors.textSecondary },
  raceDate: { fontSize: 12, color: colors.textSecondary, marginTop: 4, textTransform: 'capitalize' },
  sectionTitle: { fontSize: 18, fontWeight: '700', color: colors.text, marginBottom: 12 },
  restCard: { backgroundColor: '#1C1C1E', borderRadius: 16, padding: 28, alignItems: 'center', marginBottom: 28, gap: 8 },
  restEmoji: { fontSize: 40 },
  restTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
  restDesc: { fontSize: 14, color: colors.textSecondary, textAlign: 'center' },
  sessionCard: { backgroundColor: '#1C1C1E', borderRadius: 16, padding: 18, borderLeftWidth: 4, marginBottom: 28 },
  sessionHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  disciplineBadge: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 5, borderRadius: 20 },
  disciplineLabel: { fontSize: 13, fontWeight: '600' },
  tssChip: { backgroundColor: '#2C2C2E', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
  tssText: { fontSize: 12, color: colors.textSecondary, fontWeight: '600' },
  sessionTitle: { fontSize: 18, fontWeight: '700', color: colors.text, marginBottom: 8 },
  sessionDesc: { fontSize: 14, color: colors.textSecondary, lineHeight: 20, marginBottom: 16 },
  sessionStats: { flexDirection: 'row', gap: 20, marginBottom: 16 },
  stat: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  statText: { fontSize: 14, color: colors.textSecondary },
  gymSection: { marginBottom: 16 },
  doneButton: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 14, borderRadius: 12 },
  doneButtonText: { color: '#fff', fontWeight: '700', fontSize: 15 },
  completedBadge: { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingVertical: 14 },
  completedText: { fontSize: 15, fontWeight: '700', color: '#22C55E' },
  weekRow: { flexDirection: 'row', gap: 12 },
  weekStat: { flex: 1, backgroundColor: '#1C1C1E', borderRadius: 14, padding: 14, alignItems: 'center', gap: 4 },
  weekStatValue: { fontSize: 18, fontWeight: '800', color: colors.text },
  weekStatUnit: { fontSize: 12, fontWeight: '400', color: colors.textSecondary },
  weekStatLabel: { fontSize: 11, color: colors.textSecondary },
});
