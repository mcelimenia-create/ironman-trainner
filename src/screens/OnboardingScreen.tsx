import React, { useState } from 'react';
import {
  View, Text, TouchableOpacity, StyleSheet,
  ScrollView, ActivityIndicator, Alert, Platform,
} from 'react-native';
import DateTimePicker from '@react-native-community/datetimepicker';
import { Ionicons } from '@expo/vector-icons';
import { supabase } from '../lib/supabase';
import { useAuth } from '../lib/auth';
import { generatePlan } from '../lib/planGenerator';
import { colors } from '../lib/theme';

const RACE_TYPES = [
  { key: 'sprint',       label: 'Sprint',             desc: '750m · 20km · 5km',              icon: '⚡', color: '#22C55E' },
  { key: 'olympic',      label: 'Triatlón Olímpico',  desc: '1.5km · 40km · 10km',            icon: '🏅', color: '#3B82F6' },
  { key: 'half_ironman', label: 'Ironman 70.3',        desc: '1.9km · 90km · 21.1km',          icon: '🔶', color: '#F59E0B' },
  { key: 'full_ironman', label: 'Ironman Full',        desc: '3.8km · 180km · 42.2km',         icon: '🔴', color: '#EF4444' },
] as const;

const LEVELS = [
  { key: 'beginner',     label: 'Principiante', desc: 'Primera vez en esta distancia',         icon: '🌱' },
  { key: 'intermediate', label: 'Intermedio',   desc: 'Ya tengo base o distancias menores',     icon: '⚡' },
  { key: 'advanced',     label: 'Avanzado',     desc: 'Busco mejorar mi marca anterior',        icon: '🔥' },
] as const;

const TOTAL_STEPS = 4;

export default function OnboardingScreen({ onComplete }: { onComplete: () => void }) {
  const { session } = useAuth();
  const [step, setStep] = useState(0);
  const [raceType, setRaceType] = useState<string>('full_ironman');
  const [raceDate, setRaceDate] = useState(new Date(Date.now() + 1000 * 60 * 60 * 24 * 180));
  const [showPicker, setShowPicker] = useState(Platform.OS === 'ios');
  const [level, setLevel] = useState<'beginner' | 'intermediate' | 'advanced'>('intermediate');
  const [saving, setSaving] = useState(false);

  const weeksToRace = Math.floor((raceDate.getTime() - Date.now()) / (1000 * 60 * 60 * 24 * 7));
  const selectedRace = RACE_TYPES.find(r => r.key === raceType)!;

  const handleFinish = async () => {
    if (!session) return;
    setSaving(true);
    try {
      const { error: profileError } = await supabase.from('profiles').upsert({
        id: session.user.id,
        race_date: raceDate.toISOString().split('T')[0],
        race_type: raceType,
        level,
      });
      if (profileError) throw profileError;

      const sessions = generatePlan({
        userId: session.user.id,
        raceDate: raceDate.toISOString().split('T')[0],
        level,
        raceType,
      });

      for (let i = 0; i < sessions.length; i += 100) {
        const { error } = await supabase.from('training_sessions').insert(sessions.slice(i, i + 100));
        if (error) throw error;
      }

      onComplete();
    } catch (e: any) {
      Alert.alert('Error', e.message || 'No se pudo guardar el plan. Inténtalo de nuevo.');
      setSaving(false);
    }
  };

  const steps = [
    // Step 0: tipo de carrera
    <View key="type" style={styles.stepContainer}>
      <Text style={styles.stepEmoji}>🏁</Text>
      <Text style={styles.stepTitle}>¿Qué distancia vas a correr?</Text>
      <Text style={styles.stepDesc}>El plan de entrenamiento se ajusta a la distancia objetivo</Text>
      <View style={styles.optionsContainer}>
        {RACE_TYPES.map(r => (
          <TouchableOpacity
            key={r.key}
            style={[styles.optionCard, raceType === r.key && { borderColor: r.color, borderWidth: 2 }]}
            onPress={() => setRaceType(r.key)}
          >
            <Text style={styles.optionEmoji}>{r.icon}</Text>
            <View style={styles.optionInfo}>
              <Text style={[styles.optionLabel, raceType === r.key && { color: r.color }]}>{r.label}</Text>
              <Text style={styles.optionDesc}>{r.desc}</Text>
            </View>
            {raceType === r.key && <Ionicons name="checkmark-circle" size={22} color={r.color} />}
          </TouchableOpacity>
        ))}
      </View>
    </View>,

    // Step 1: fecha
    <View key="date" style={styles.stepContainer}>
      <Text style={styles.stepEmoji}>📅</Text>
      <Text style={styles.stepTitle}>¿Cuándo es la carrera?</Text>
      <Text style={styles.stepDesc}>El plan empieza hoy y termina el día de la carrera</Text>
      <View style={styles.dateCard}>
        <Text style={styles.dateDisplay}>
          {raceDate.toLocaleDateString('es-ES', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
        </Text>
        <View style={styles.weeksChip}>
          <Ionicons name="time-outline" size={14} color="#EF4444" />
          <Text style={styles.weeksText}>{weeksToRace} semanas de entrenamiento</Text>
        </View>
      </View>
      {Platform.OS === 'android' && (
        <TouchableOpacity style={styles.dateButton} onPress={() => setShowPicker(true)}>
          <Ionicons name="calendar-outline" size={18} color="#fff" />
          <Text style={styles.dateButtonText}>Cambiar fecha</Text>
        </TouchableOpacity>
      )}
      {(showPicker || Platform.OS === 'ios') && (
        <DateTimePicker
          value={raceDate}
          mode="date"
          display={Platform.OS === 'ios' ? 'spinner' : 'default'}
          minimumDate={new Date(Date.now() + 1000 * 60 * 60 * 24 * 30)}
          maximumDate={new Date(Date.now() + 1000 * 60 * 60 * 24 * 730)}
          onChange={(_, date) => {
            setShowPicker(Platform.OS === 'ios');
            if (date) setRaceDate(date);
          }}
          themeVariant="dark"
          style={styles.datePicker}
        />
      )}
    </View>,

    // Step 2: nivel
    <View key="level" style={styles.stepContainer}>
      <Text style={styles.stepEmoji}>🎯</Text>
      <Text style={styles.stepTitle}>¿Cuál es tu nivel?</Text>
      <Text style={styles.stepDesc}>Ajusta el volumen e intensidad del plan</Text>
      <View style={styles.optionsContainer}>
        {LEVELS.map(l => (
          <TouchableOpacity
            key={l.key}
            style={[styles.optionCard, level === l.key && styles.optionCardActive]}
            onPress={() => setLevel(l.key)}
          >
            <Text style={styles.optionEmoji}>{l.icon}</Text>
            <View style={styles.optionInfo}>
              <Text style={[styles.optionLabel, level === l.key && { color: '#EF4444' }]}>{l.label}</Text>
              <Text style={styles.optionDesc}>{l.desc}</Text>
            </View>
            {level === l.key && <Ionicons name="checkmark-circle" size={22} color="#EF4444" />}
          </TouchableOpacity>
        ))}
      </View>
    </View>,

    // Step 3: resumen
    <View key="summary" style={styles.stepContainer}>
      <Text style={styles.stepEmoji}>🚀</Text>
      <Text style={styles.stepTitle}>¡Tu plan está listo!</Text>
      <Text style={styles.stepDesc}>{weeksToRace} semanas de entrenamiento personalizado</Text>
      <View style={styles.summaryCard}>
        <SummaryRow icon="flag"      label="Distancia"      value={selectedRace.label} />
        <SummaryRow icon="calendar"  label="Fecha carrera"  value={raceDate.toLocaleDateString('es-ES', { day: 'numeric', month: 'long', year: 'numeric' })} />
        <SummaryRow icon="time"      label="Duración plan"  value={`${weeksToRace} semanas`} />
        <SummaryRow icon="trophy"    label="Nivel"          value={LEVELS.find(l => l.key === level)?.label || ''} />
        <SummaryRow icon="fitness"   label="Fuerza"         value="2 sesiones/semana" />
        <SummaryRow icon="reload"    label="Fases"          value="Base → Build → Peak → Taper" />
      </View>
      <Text style={styles.noteText}>
        Podrás ajustar tus umbrales (FTP, ritmo, CSS) en el perfil para afinar las zonas.
      </Text>
    </View>,
  ];

  return (
    <View style={styles.container}>
      <View style={styles.progressRow}>
        {Array.from({ length: TOTAL_STEPS }).map((_, i) => (
          <View key={i} style={[styles.progressDot, i <= step && styles.progressDotActive]} />
        ))}
      </View>

      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>
        {steps[step]}
      </ScrollView>

      <View style={styles.footer}>
        {step > 0 && (
          <TouchableOpacity style={styles.backButton} onPress={() => setStep(s => s - 1)}>
            <Ionicons name="arrow-back" size={20} color={colors.textSecondary} />
          </TouchableOpacity>
        )}
        <TouchableOpacity
          style={[styles.nextButton, saving && styles.buttonDisabled]}
          onPress={step < TOTAL_STEPS - 1 ? () => setStep(s => s + 1) : handleFinish}
          disabled={saving}
        >
          {saving
            ? <ActivityIndicator color="#fff" />
            : <>
                <Text style={styles.nextButtonText}>
                  {step < TOTAL_STEPS - 1 ? 'Siguiente' : 'Empezar a entrenar'}
                </Text>
                <Ionicons name="arrow-forward" size={18} color="#fff" />
              </>
          }
        </TouchableOpacity>
      </View>
    </View>
  );
}

function SummaryRow({ icon, label, value }: { icon: string; label: string; value: string }) {
  return (
    <View style={styles.summaryRow}>
      <Ionicons name={icon as any} size={16} color="#EF4444" />
      <Text style={styles.summaryLabel}>{label}</Text>
      <Text style={styles.summaryValue}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  progressRow: { flexDirection: 'row', justifyContent: 'center', gap: 8, paddingTop: 60, paddingBottom: 8 },
  progressDot: { width: 8, height: 8, borderRadius: 4, backgroundColor: '#2C2C2E' },
  progressDotActive: { backgroundColor: '#EF4444', width: 24 },
  scroll: { padding: 28, paddingBottom: 20 },
  stepContainer: { alignItems: 'center' },
  stepEmoji: { fontSize: 52, marginBottom: 16 },
  stepTitle: { fontSize: 24, fontWeight: '800', color: colors.text, textAlign: 'center', marginBottom: 10 },
  stepDesc: { fontSize: 15, color: colors.textSecondary, textAlign: 'center', lineHeight: 22, marginBottom: 28 },
  optionsContainer: { width: '100%', gap: 10 },
  optionCard: { flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: '#1C1C1E', borderRadius: 14, padding: 16, borderWidth: 2, borderColor: 'transparent' },
  optionCardActive: { borderColor: '#EF4444' },
  optionEmoji: { fontSize: 26 },
  optionInfo: { flex: 1 },
  optionLabel: { fontSize: 16, fontWeight: '700', color: colors.text },
  optionDesc: { fontSize: 13, color: colors.textSecondary, marginTop: 2 },
  dateCard: { backgroundColor: '#1C1C1E', borderRadius: 16, padding: 20, width: '100%', alignItems: 'center', gap: 10, marginBottom: 16 },
  dateDisplay: { fontSize: 17, fontWeight: '700', color: colors.text, textAlign: 'center', textTransform: 'capitalize' },
  weeksChip: { flexDirection: 'row', alignItems: 'center', gap: 5, backgroundColor: '#EF444420', paddingHorizontal: 12, paddingVertical: 5, borderRadius: 20 },
  weeksText: { fontSize: 14, color: '#EF4444', fontWeight: '700' },
  dateButton: { flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: '#1C1C1E', paddingHorizontal: 20, paddingVertical: 12, borderRadius: 12, marginBottom: 16 },
  dateButtonText: { color: '#fff', fontWeight: '600' },
  datePicker: { width: '100%' },
  summaryCard: { width: '100%', backgroundColor: '#1C1C1E', borderRadius: 16, padding: 16, marginBottom: 16 },
  summaryRow: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingVertical: 11, borderBottomWidth: 1, borderBottomColor: '#2C2C2E' },
  summaryLabel: { flex: 1, fontSize: 14, color: colors.textSecondary },
  summaryValue: { fontSize: 14, fontWeight: '600', color: colors.text },
  noteText: { fontSize: 13, color: colors.textSecondary, textAlign: 'center', lineHeight: 20 },
  footer: { flexDirection: 'row', gap: 12, padding: 24, paddingBottom: 40 },
  backButton: { width: 52, height: 52, borderRadius: 14, backgroundColor: '#1C1C1E', alignItems: 'center', justifyContent: 'center' },
  nextButton: { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, backgroundColor: '#EF4444', borderRadius: 14, height: 52 },
  buttonDisabled: { opacity: 0.6 },
  nextButtonText: { color: '#fff', fontSize: 16, fontWeight: '700' },
});
