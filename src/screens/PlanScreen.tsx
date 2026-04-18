import React, { useState } from 'react';
import { View, Text, ScrollView, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors } from '../lib/theme';
import { useWeekSessions } from '../hooks/useWeekSessions';
import { supabase } from '../lib/supabase';
import { useRefresh } from '../lib/refreshContext';
import type { Discipline } from '../types';

const DISC: Record<Discipline, { icon: string; color: string; label: string }> = {
  swim:  { icon: 'water',    color: '#3B82F6', label: 'Natación' },
  bike:  { icon: 'bicycle',  color: '#F59E0B', label: 'Bici' },
  run:   { icon: 'walk',     color: '#EF4444', label: 'Carrera' },
  gym:   { icon: 'barbell',  color: '#8B5CF6', label: 'Fuerza' },
  brick: { icon: 'flash',    color: '#F97316', label: 'Brick' },
  rest:  { icon: 'moon',     color: '#6B7280', label: 'Descanso' },
};

const PHASES = ['Todas', 'Base', 'Build', 'Peak', 'Taper'];

const PHASE_COLORS: Record<string, string> = {
  Base: '#3B82F6', Build: '#F59E0B', Peak: '#EF4444', Taper: '#22C55E',
};

export default function PlanScreen() {
  const { weeks, loading, updateSession } = useWeekSessions();
  const { refresh } = useRefresh();
  const [selectedPhase, setSelectedPhase] = useState('Todas');
  const [togglingId, setTogglingId] = useState<string | null>(null);

  const filteredWeeks = selectedPhase === 'Todas'
    ? weeks
    : weeks.filter(w => w.phase === selectedPhase);

  const totalWeeks = weeks.length;
  const completedWeeks = weeks.filter(w => w.sessions.every(s => s.completed || new Date(s.date) > new Date())).length;
  const progressPct = totalWeeks > 0 ? Math.round((completedWeeks / totalWeeks) * 100) : 0;

  const currentWeek = weeks.find(w => {
    const start = new Date(w.weekStart);
    const end = new Date(start); end.setDate(start.getDate() + 6);
    const now = new Date();
    return now >= start && now <= end;
  }) || weeks[0];

  const toggleSession = (id: string, completed: boolean) => {
    setTogglingId(id);
    supabase.from('training_sessions').update({ completed: !completed }).eq('id', id).then(() => {
      updateSession(id, !completed);
      setTogglingId(null);
    });
  };

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator color={colors.accent} size="large" />
      </View>
    );
  }

  if (weeks.length === 0) {
    return (
      <View style={styles.centered}>
        <Text style={styles.emptyEmoji}>📋</Text>
        <Text style={styles.emptyTitle}>Sin plan todavía</Text>
        <Text style={styles.emptyDesc}>Ve a Perfil para generar tu plan de entrenamiento</Text>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Plan de entrenamiento</Text>

      {/* Fases */}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.phasesRow}>
        {PHASES.map(p => (
          <TouchableOpacity
            key={p}
            style={[styles.phaseChip, selectedPhase === p && styles.phaseChipActive]}
            onPress={() => setSelectedPhase(p)}
          >
            <Text style={[styles.phaseChipText, selectedPhase === p && styles.phaseChipTextActive]}>{p}</Text>
          </TouchableOpacity>
        ))}
      </ScrollView>

      {/* Progreso global */}
      <View style={styles.progressCard}>
        <View style={styles.progressHeader}>
          <Text style={styles.progressTitle}>
            Semana {currentWeek?.weekNumber || 1} / {totalWeeks}
          </Text>
          <Text style={[styles.progressPhase, { color: PHASE_COLORS[currentWeek?.phase] || '#EF4444' }]}>
            {currentWeek?.phase?.toUpperCase() || 'BASE'}
          </Text>
        </View>
        <View style={styles.progressBarBg}>
          <View style={[styles.progressFill, { width: `${Math.max(progressPct, 2)}%` as any }]} />
        </View>
        <Text style={styles.progressSub}>{progressPct}% completado</Text>
      </View>

      {/* Semanas */}
      {filteredWeeks.map(week => {
        const phaseColor = PHASE_COLORS[week.phase] || '#EF4444';
        const weekTss = week.sessions.reduce((s, r) => s + (r.tss || 0), 0);
        const weekDone = week.sessions.filter(s => s.completed).length;
        return (
          <View key={week.weekStart} style={styles.weekCard}>
            <View style={styles.weekHeader}>
              <Text style={styles.weekTitle}>Semana {week.weekNumber}</Text>
              <View style={[styles.weekBadge, { backgroundColor: phaseColor + '20' }]}>
                <Text style={[styles.weekBadgeText, { color: phaseColor }]}>{week.phase}</Text>
              </View>
              <Text style={styles.weekMeta}>{weekDone}/{week.sessions.length} · TSS {weekTss}</Text>
            </View>

            {week.sessions.map(s => {
              const disc = DISC[s.discipline as Discipline] || DISC.rest;
              const dayName = new Date(s.date + 'T12:00:00').toLocaleDateString('es-ES', { weekday: 'short' });
              const dayNum = new Date(s.date + 'T12:00:00').getDate();
              const isPast = new Date(s.date) < new Date();
              return (
                <TouchableOpacity
                  key={s.id}
                  style={[styles.sessionRow, s.completed && styles.sessionRowDone]}
                  onPress={() => toggleSession(s.id, s.completed)}
                  activeOpacity={0.7}
                >
                  <View style={[styles.dayBadge, { backgroundColor: disc.color + '15' }]}>
                    <Text style={[styles.dayName, { color: disc.color }]}>{dayName}</Text>
                    <Text style={[styles.dayNum, { color: disc.color }]}>{dayNum}</Text>
                  </View>
                  <Ionicons name={disc.icon as any} size={17} color={s.completed ? '#3C3C3E' : disc.color} />
                  <View style={styles.sessionInfo}>
                    <Text style={[styles.sessionTitle, s.completed && styles.textDone]} numberOfLines={1}>
                      {s.title}
                    </Text>
                    <Text style={styles.sessionMeta}>
                      {s.duration_min}min{s.distance_km ? ` · ${s.distance_km}km` : ''}
                    </Text>
                  </View>
                  {togglingId === s.id
                    ? <ActivityIndicator size="small" color={colors.accent} />
                    : s.completed
                      ? <Ionicons name="checkmark-circle" size={22} color="#22C55E" />
                      : isPast
                        ? <Ionicons name="ellipse-outline" size={22} color="#EF4444" />
                        : <Ionicons name="ellipse-outline" size={22} color="#3C3C3E" />
                  }
                </TouchableOpacity>
              );
            })}
          </View>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: 20, paddingBottom: 40 },
  centered: { flex: 1, backgroundColor: colors.background, alignItems: 'center', justifyContent: 'center', gap: 12 },
  emptyEmoji: { fontSize: 48 },
  emptyTitle: { fontSize: 18, fontWeight: '700', color: colors.text },
  emptyDesc: { fontSize: 14, color: colors.textSecondary, textAlign: 'center', paddingHorizontal: 40 },
  title: { fontSize: 26, fontWeight: '700', color: colors.text, marginBottom: 16 },
  phasesRow: { marginBottom: 20 },
  phaseChip: { paddingHorizontal: 16, paddingVertical: 8, borderRadius: 20, backgroundColor: '#1C1C1E', marginRight: 8, borderWidth: 1, borderColor: '#2C2C2E' },
  phaseChipActive: { backgroundColor: '#EF4444', borderColor: '#EF4444' },
  phaseChipText: { fontSize: 13, fontWeight: '600', color: colors.textSecondary },
  phaseChipTextActive: { color: '#fff' },
  progressCard: { backgroundColor: '#1C1C1E', borderRadius: 16, padding: 18, marginBottom: 24 },
  progressHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 },
  progressTitle: { fontSize: 16, fontWeight: '700', color: colors.text },
  progressPhase: { fontSize: 11, fontWeight: '700', letterSpacing: 1 },
  progressBarBg: { height: 8, backgroundColor: '#2C2C2E', borderRadius: 4, overflow: 'hidden' },
  progressFill: { height: 8, backgroundColor: '#EF4444', borderRadius: 4 },
  progressSub: { fontSize: 12, color: colors.textSecondary, marginTop: 8 },
  weekCard: { backgroundColor: '#1C1C1E', borderRadius: 16, padding: 16, marginBottom: 14 },
  weekHeader: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 12 },
  weekTitle: { fontSize: 15, fontWeight: '700', color: colors.text },
  weekBadge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8 },
  weekBadgeText: { fontSize: 11, fontWeight: '700' },
  weekMeta: { marginLeft: 'auto' as any, fontSize: 11, color: colors.textSecondary },
  sessionRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 9, borderBottomWidth: 1, borderBottomColor: '#2C2C2E' },
  sessionRowDone: { opacity: 0.45 },
  dayBadge: { width: 42, borderRadius: 8, alignItems: 'center', paddingVertical: 4 },
  dayName: { fontSize: 10, fontWeight: '700', textTransform: 'uppercase' },
  dayNum: { fontSize: 15, fontWeight: '800' },
  sessionInfo: { flex: 1 },
  sessionTitle: { fontSize: 13, fontWeight: '600', color: colors.text },
  textDone: { textDecorationLine: 'line-through', color: colors.textSecondary },
  sessionMeta: { fontSize: 11, color: colors.textSecondary, marginTop: 2 },
});
