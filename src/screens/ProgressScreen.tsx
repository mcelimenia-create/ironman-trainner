import React, { useState } from 'react';
import { View, Text, ScrollView, TouchableOpacity, StyleSheet, ActivityIndicator } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors } from '../lib/theme';
import { useActivities } from '../hooks/useActivities';
import { useWeekStats } from '../hooks/useWeekStats';
import type { Discipline } from '../types';

const DISC: Record<string, { icon: string; color: string; label: string }> = {
  swim:  { icon: 'water',    color: '#3B82F6', label: 'Natación' },
  bike:  { icon: 'bicycle',  color: '#F59E0B', label: 'Bici' },
  run:   { icon: 'walk',     color: '#EF4444', label: 'Carrera' },
  gym:   { icon: 'barbell',  color: '#8B5CF6', label: 'Fuerza' },
  brick: { icon: 'flash',    color: '#F97316', label: 'Brick' },
  rest:  { icon: 'moon',     color: '#6B7280', label: 'Descanso' },
};

const TABS = ['Actividades', 'Métricas'];

export default function ProgressScreen() {
  const [tab, setTab] = useState('Actividades');
  const { activities, loading } = useActivities();
  const weekStats = useWeekStats();

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Progreso</Text>

      <View style={styles.tabs}>
        {TABS.map(t => (
          <TouchableOpacity
            key={t}
            style={[styles.tab, tab === t && styles.tabActive]}
            onPress={() => setTab(t)}
          >
            <Text style={[styles.tabText, tab === t && styles.tabTextActive]}>{t}</Text>
          </TouchableOpacity>
        ))}
      </View>

      {tab === 'Actividades' && (
        <>
          {/* Resumen semana actual */}
          <Text style={styles.sectionTitle}>Esta semana</Text>
          <View style={styles.summaryRow}>
            <SummaryCard label="Horas" value={weekStats ? `${weekStats.totalHours}` : '—'} unit="h" color="#EF4444" />
            <SummaryCard label="TSS" value={weekStats ? `${weekStats.tssTotal}` : '—'} unit="" color="#F59E0B" />
            <SummaryCard label="Sesiones" value={weekStats ? `${weekStats.sessionsCompleted}/${weekStats.sessionsTotal}` : '—'} unit="" color="#22C55E" />
          </View>

          {/* Volumen semanal */}
          {weekStats && (
            <View style={styles.volumeRow}>
              <VolumeChip icon="water"   color="#3B82F6" value={`${weekStats.swimKm}km`} label="Swim" />
              <VolumeChip icon="bicycle" color="#F59E0B" value={`${weekStats.bikeKm}km`} label="Bike" />
              <VolumeChip icon="walk"    color="#EF4444" value={`${weekStats.runKm}km`}  label="Run" />
            </View>
          )}

          <Text style={[styles.sectionTitle, { marginTop: 24 }]}>Actividades registradas</Text>

          {loading ? (
            <ActivityIndicator color={colors.accent} style={{ marginTop: 40 }} />
          ) : activities.length === 0 ? (
            <View style={styles.empty}>
              <Ionicons name="bicycle-outline" size={48} color={colors.textSecondary} />
              <Text style={styles.emptyTitle}>Sin actividades aún</Text>
              <Text style={styles.emptyDesc}>Conecta Strava en tu perfil para importar automáticamente</Text>
            </View>
          ) : (
            activities.map(a => {
              const disc = DISC[a.discipline] || DISC.run;
              const date = new Date(a.date).toLocaleDateString('es-ES', { weekday: 'short', day: 'numeric', month: 'short' });
              return (
                <View key={a.id} style={styles.activityCard}>
                  <View style={[styles.discBar, { backgroundColor: disc.color }]} />
                  <View style={styles.activityBody}>
                    <View style={styles.activityTop}>
                      <View style={[styles.disciplineTag, { backgroundColor: disc.color + '20' }]}>
                        <Ionicons name={disc.icon as any} size={12} color={disc.color} />
                        <Text style={[styles.disciplineTagText, { color: disc.color }]}>{disc.label}</Text>
                      </View>
                      <Text style={styles.activityDate}>{date}</Text>
                    </View>
                    <Text style={styles.activityTitle}>{a.title}</Text>
                    <View style={styles.activityStats}>
                      {a.distance_km > 0 && <Stat icon="navigate-outline" value={`${a.distance_km} km`} />}
                      <Stat icon="time-outline" value={`${a.duration_min} min`} />
                      {a.avg_hr && <Stat icon="heart-outline" value={`${a.avg_hr} bpm`} />}
                      {a.tss && (
                        <View style={styles.tssBadge}>
                          <Text style={styles.tssBadgeText}>TSS {a.tss}</Text>
                        </View>
                      )}
                    </View>
                  </View>
                </View>
              );
            })
          )}
        </>
      )}

      {tab === 'Métricas' && (
        <View style={styles.metricsContainer}>
          <MetricCard
            title="Carga crónica (CTL)"
            description="Forma física acumulada en ~6 semanas"
            value="—"
            unit="TSS/día"
            color="#3B82F6"
            icon="trending-up"
          />
          <MetricCard
            title="Carga aguda (ATL)"
            description="Fatiga de los últimos 7 días"
            value="—"
            unit="TSS/día"
            color="#EF4444"
            icon="flame"
          />
          <MetricCard
            title="Forma (TSB)"
            description="CTL - ATL. Positivo = fresco, negativo = cansado"
            value="—"
            unit=""
            color="#22C55E"
            icon="fitness"
          />
          <View style={styles.metricsNote}>
            <Ionicons name="information-circle-outline" size={18} color={colors.textSecondary} />
            <Text style={styles.metricsNoteText}>
              Las métricas CTL/ATL/TSB se calcularán cuando tengas actividades registradas con Strava
            </Text>
          </View>
        </View>
      )}
    </ScrollView>
  );
}

function SummaryCard({ label, value, unit, color }: any) {
  return (
    <View style={[styles.summaryCard, { borderTopColor: color }]}>
      <Text style={styles.summaryValue}>{value}<Text style={styles.summaryUnit}>{unit}</Text></Text>
      <Text style={styles.summaryLabel}>{label}</Text>
    </View>
  );
}

function VolumeChip({ icon, color, value, label }: any) {
  return (
    <View style={styles.volumeChip}>
      <Ionicons name={icon} size={16} color={color} />
      <Text style={[styles.volumeValue, { color }]}>{value}</Text>
      <Text style={styles.volumeLabel}>{label}</Text>
    </View>
  );
}

function Stat({ icon, value }: any) {
  return (
    <View style={styles.statRow}>
      <Ionicons name={icon} size={12} color={colors.textSecondary} />
      <Text style={styles.statText}>{value}</Text>
    </View>
  );
}

function MetricCard({ title, description, value, unit, color, icon }: any) {
  return (
    <View style={[styles.metricCard, { borderLeftColor: color }]}>
      <View style={styles.metricHeader}>
        <Ionicons name={icon} size={18} color={color} />
        <Text style={styles.metricTitle}>{title}</Text>
        <Text style={[styles.metricValue, { color }]}>{value}{unit && ` ${unit}`}</Text>
      </View>
      <Text style={styles.metricDesc}>{description}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: 20, paddingBottom: 40 },
  title: { fontSize: 26, fontWeight: '700', color: colors.text, marginBottom: 16 },
  tabs: { flexDirection: 'row', backgroundColor: '#1C1C1E', borderRadius: 12, padding: 4, marginBottom: 24 },
  tab: { flex: 1, paddingVertical: 10, alignItems: 'center', borderRadius: 10 },
  tabActive: { backgroundColor: '#EF4444' },
  tabText: { fontSize: 14, fontWeight: '600', color: colors.textSecondary },
  tabTextActive: { color: '#fff' },
  sectionTitle: { fontSize: 15, fontWeight: '700', color: colors.text, marginBottom: 12 },
  summaryRow: { flexDirection: 'row', gap: 10, marginBottom: 12 },
  summaryCard: { flex: 1, backgroundColor: '#1C1C1E', borderRadius: 14, padding: 14, borderTopWidth: 3, alignItems: 'center' },
  summaryValue: { fontSize: 20, fontWeight: '800', color: colors.text },
  summaryUnit: { fontSize: 12, fontWeight: '400', color: colors.textSecondary },
  summaryLabel: { fontSize: 11, color: colors.textSecondary, marginTop: 3 },
  volumeRow: { flexDirection: 'row', gap: 10 },
  volumeChip: { flex: 1, backgroundColor: '#1C1C1E', borderRadius: 12, padding: 12, alignItems: 'center', gap: 4 },
  volumeValue: { fontSize: 15, fontWeight: '800' },
  volumeLabel: { fontSize: 10, color: colors.textSecondary },
  empty: { alignItems: 'center', paddingVertical: 50, gap: 10 },
  emptyTitle: { fontSize: 16, fontWeight: '700', color: colors.text },
  emptyDesc: { fontSize: 13, color: colors.textSecondary, textAlign: 'center', lineHeight: 20 },
  activityCard: { flexDirection: 'row', backgroundColor: '#1C1C1E', borderRadius: 14, marginBottom: 10, overflow: 'hidden' },
  discBar: { width: 4 },
  activityBody: { flex: 1, padding: 14, gap: 6 },
  activityTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  disciplineTag: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 10 },
  disciplineTagText: { fontSize: 11, fontWeight: '700' },
  activityDate: { fontSize: 11, color: colors.textSecondary, textTransform: 'capitalize' },
  activityTitle: { fontSize: 14, fontWeight: '700', color: colors.text },
  activityStats: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, alignItems: 'center' },
  statRow: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  statText: { fontSize: 12, color: colors.textSecondary },
  tssBadge: { backgroundColor: '#2C2C2E', paddingHorizontal: 8, paddingVertical: 2, borderRadius: 8 },
  tssBadgeText: { fontSize: 11, color: colors.textSecondary, fontWeight: '600' },
  metricsContainer: { gap: 12 },
  metricCard: { backgroundColor: '#1C1C1E', borderRadius: 14, padding: 16, borderLeftWidth: 4, gap: 6 },
  metricHeader: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  metricTitle: { flex: 1, fontSize: 14, fontWeight: '700', color: colors.text },
  metricValue: { fontSize: 18, fontWeight: '800' },
  metricDesc: { fontSize: 12, color: colors.textSecondary, lineHeight: 18 },
  metricsNote: { flexDirection: 'row', gap: 8, backgroundColor: '#1C1C1E', borderRadius: 12, padding: 14, alignItems: 'flex-start' },
  metricsNoteText: { flex: 1, fontSize: 13, color: colors.textSecondary, lineHeight: 18 },
});
