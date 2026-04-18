import React, { useState, useEffect } from 'react';
import {
  View, Text, ScrollView, TouchableOpacity, StyleSheet,
  Alert, Modal, Platform, ActivityIndicator, TextInput, KeyboardAvoidingView,
} from 'react-native';
import * as WebBrowser from 'expo-web-browser';
import DateTimePicker from '@react-native-community/datetimepicker';
import { Ionicons } from '@expo/vector-icons';
import { useNavigation } from '@react-navigation/native';
import { colors } from '../lib/theme';
import { useAuth } from '../lib/auth';
import { useProfile } from '../hooks/useProfile';
import { useRefresh } from '../lib/refreshContext';
import { supabase } from '../lib/supabase';
import { generatePlan } from '../lib/planGenerator';

const RACE_TYPES = [
  { key: 'sprint',       label: 'Sprint',            desc: '750m · 20km · 5km',      icon: '⚡', color: '#22C55E' },
  { key: 'olympic',      label: 'Triatlón Olímpico', desc: '1.5km · 40km · 10km',    icon: '🏅', color: '#3B82F6' },
  { key: 'half_ironman', label: 'Ironman 70.3',       desc: '1.9km · 90km · 21.1km',  icon: '🔶', color: '#F59E0B' },
  { key: 'full_ironman', label: 'Ironman Full',       desc: '3.8km · 180km · 42.2km', icon: '🔴', color: '#EF4444' },
];

export default function ProfileScreen() {
  const { signOut, session } = useAuth();
  const { profile, loading } = useProfile();
  const { refresh } = useRefresh();
  const navigation = useNavigation<any>();

  const [editModal, setEditModal] = useState<'race_type' | 'race_date' | null>(null);
  const [tempDate, setTempDate] = useState(new Date());
  const [tempRaceType, setTempRaceType] = useState('full_ironman');
  const [saving, setSaving] = useState(false);
  const [thresholdModal, setThresholdModal] = useState<{ field: string; label: string; unit: string; value: string } | null>(null);
  const [thresholdInput, setThresholdInput] = useState('');

  const connectStrava = async () => {
    if (!session) return;
    const authUrl = `https://ironman-trainner-production.up.railway.app/app/strava/auth/${session.user.id}`;
    await WebBrowser.openBrowserAsync(authUrl);
    // Al volver, refrescamos el perfil para ver si se conectó
    refresh();
  };

  const openThreshold = (field: string, label: string, unit: string, current?: number) => {
    setThresholdInput(current ? `${current}` : '');
    setThresholdModal({ field, label, unit, value: current ? `${current}` : '' });
  };

  const saveThreshold = async () => {
    if (!session || !thresholdModal) return;
    const val = parseInt(thresholdInput);
    if (isNaN(val) || val <= 0) { Alert.alert('Valor no válido'); return; }
    await supabase.from('profiles').update({ [thresholdModal.field]: val }).eq('id', session.user.id);
    refresh();
    setThresholdModal(null);
  };

  const handleSignOut = () => {
    Alert.alert('Cerrar sesión', '¿Seguro?', [
      { text: 'Cancelar', style: 'cancel' },
      { text: 'Salir', style: 'destructive', onPress: signOut },
    ]);
  };

  const openDateEdit = () => {
    setTempDate(profile?.race_date ? new Date(profile.race_date + 'T12:00:00') : new Date(Date.now() + 1000 * 60 * 60 * 24 * 180));
    setEditModal('race_date');
  };

  const openTypeEdit = () => {
    setTempRaceType(profile?.race_type || 'full_ironman');
    setEditModal('race_type');
  };

  const doSave = async (field: 'race_date' | 'race_type', regenerate: boolean) => {
    if (!session || !profile) return;
    setEditModal(null);
    setSaving(true);
    try {
      const newRaceDate = field === 'race_date' ? tempDate.toISOString().split('T')[0] : profile.race_date;
      const newRaceType = field === 'race_type' ? tempRaceType : (profile.race_type || 'full_ironman');

      const { error: updateError } = await supabase
        .from('profiles')
        .update({ race_date: newRaceDate, race_type: newRaceType })
        .eq('id', session.user.id);

      if (updateError) throw updateError;

      if (regenerate) {
        await supabase.from('training_sessions').delete().eq('user_id', session.user.id);
        const level = (profile.level || 'intermediate') as 'beginner' | 'intermediate' | 'advanced';
        const sessions = generatePlan({ userId: session.user.id, raceDate: newRaceDate, level, raceType: newRaceType as any });
        for (let i = 0; i < sessions.length; i += 100) {
          const { error } = await supabase.from('training_sessions').insert(sessions.slice(i, i + 100));
          if (error) throw error;
        }
      }

      // Refresca todos los hooks y navega a Hoy
      refresh();
      navigation.navigate('Hoy');
    } catch (e: any) {
      Alert.alert('Error al guardar', e.message || 'Inténtalo de nuevo');
    } finally {
      setSaving(false);
    }
  };

  const saveChanges = (field: 'race_date' | 'race_type') => {
    Alert.alert(
      '¿Qué quieres hacer?',
      'Puedes guardar el cambio o regenerar el plan completo desde cero.',
      [
        { text: 'Cancelar', style: 'cancel' },
        { text: 'Solo guardar', onPress: () => doSave(field, false) },
        { text: 'Regenerar plan', onPress: () => doSave(field, true) },
      ]
    );
  };

  const raceInfo = RACE_TYPES.find(r => r.key === (profile?.race_type || 'full_ironman'))!;
  const daysToRace = profile?.race_date
    ? Math.max(0, Math.floor((new Date(profile.race_date + 'T12:00:00').getTime() - Date.now()) / (1000 * 60 * 60 * 24)))
    : null;

  const firstName = profile?.name?.split(' ')[0] || '—';
  const initial = (profile?.name || 'A')[0].toUpperCase();

  if (saving) {
    return (
      <View style={styles.loaderScreen}>
        <ActivityIndicator color={colors.accent} size="large" />
        <Text style={styles.loaderText}>Guardando cambios...</Text>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={styles.content}>
      <Text style={styles.title}>Perfil</Text>

      {/* Avatar */}
      <View style={styles.avatarSection}>
        <View style={styles.avatar}>
          <Text style={styles.avatarText}>{initial}</Text>
        </View>
        <Text style={styles.name}>{profile?.name || '—'}</Text>
        <Text style={styles.email}>{session?.user.email}</Text>
        <View style={styles.levelBadge}>
          <Text style={styles.levelText}>{(profile?.level || 'intermedio').toUpperCase()}</Text>
        </View>
      </View>

      {/* Carrera objetivo */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Carrera objetivo</Text>
        <View style={styles.raceCard}>
          {/* Tipo */}
          <TouchableOpacity style={styles.raceRow} onPress={openTypeEdit}>
            <Text style={styles.raceEmoji}>{raceInfo.icon}</Text>
            <View style={styles.raceInfo}>
              <Text style={[styles.raceLabel, { color: raceInfo.color }]}>{raceInfo.label}</Text>
              <Text style={styles.raceDesc}>{raceInfo.desc}</Text>
            </View>
            <Ionicons name="pencil-outline" size={18} color={colors.textSecondary} />
          </TouchableOpacity>

          <View style={styles.divider} />

          {/* Fecha */}
          <TouchableOpacity style={styles.raceRow} onPress={openDateEdit}>
            <Ionicons name="calendar-outline" size={22} color={colors.textSecondary} />
            <View style={styles.raceInfo}>
              <Text style={styles.raceDateLabel}>Fecha de carrera</Text>
              <Text style={styles.raceDateValue}>
                {profile?.race_date
                  ? new Date(profile.race_date + 'T12:00:00').toLocaleDateString('es-ES', { day: 'numeric', month: 'long', year: 'numeric' })
                  : 'No fijada'}
              </Text>
            </View>
            {daysToRace !== null && (
              <View style={styles.daysChip}>
                <Text style={styles.daysText}>{daysToRace}d</Text>
              </View>
            )}
            <Ionicons name="pencil-outline" size={18} color={colors.textSecondary} />
          </TouchableOpacity>
        </View>
      </View>

      {/* Umbrales */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Umbrales de rendimiento</Text>
        <View style={styles.thresholdsGrid}>
          <ThresholdCard icon="bicycle" color="#F59E0B" label="FTP Bici"       value={profile?.ftp ? `${profile.ftp}` : '—'} unit="W"     onPress={() => openThreshold('ftp', 'FTP Bici', 'W', profile?.ftp)} />
          <ThresholdCard icon="walk"    color="#EF4444" label="FC máxima"      value={profile?.max_hr ? `${profile.max_hr}` : '—'} unit="bpm" onPress={() => openThreshold('max_hr', 'FC máxima', 'bpm', profile?.max_hr)} />
          <ThresholdCard icon="water"   color="#3B82F6" label="Peso"           value={profile?.weight_kg ? `${profile.weight_kg}` : '—'} unit="kg" onPress={() => openThreshold('weight_kg', 'Peso', 'kg', profile?.weight_kg)} />
          <ThresholdCard icon="heart"   color="#EC4899" label="FC reposo"      value="—" unit="bpm"  onPress={() => Alert.alert('Próximamente', 'En la próxima actualización')} />
        </View>
      </View>

      {/* Conexiones */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Conexiones</Text>
        <TouchableOpacity style={styles.connectRow} onPress={profile?.strava_connected ? undefined : connectStrava}>
          <View style={[styles.connectIcon, { backgroundColor: '#FC4C0220' }]}>
            <Ionicons name="bicycle" size={22} color="#FC4C02" />
          </View>
          <View style={styles.connectInfo}>
            <Text style={styles.connectName}>Strava</Text>
            <Text style={[styles.connectStatus, profile?.strava_connected && { color: '#22C55E' }]}>
              {profile?.strava_connected ? 'Conectado ✓' : 'No conectado'}
            </Text>
          </View>
          {!profile?.strava_connected && (
            <View style={styles.connectButton}>
              <Text style={styles.connectButtonText}>Conectar</Text>
            </View>
          )}
        </TouchableOpacity>
        <View style={styles.connectRow}>
          <View style={[styles.connectIcon, { backgroundColor: '#007AFF20' }]}>
            <Ionicons name="watch-outline" size={22} color="#007AFF" />
          </View>
          <View style={styles.connectInfo}>
            <Text style={styles.connectName}>Garmin</Text>
            <Text style={styles.connectStatus}>Pendiente de aprobación</Text>
          </View>
          <View style={[styles.connectButton, styles.connectButtonDisabled]}>
            <Text style={styles.connectButtonTextDisabled}>Pronto</Text>
          </View>
        </View>
      </View>

      {/* Ajustes */}
      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Ajustes</Text>
        <SettingRow icon="notifications-outline" label="Notificaciones" onPress={() => Alert.alert('Próximamente', 'Las notificaciones llegarán en la próxima actualización.')} />
        <SettingRow icon="help-circle-outline"   label="Ayuda y soporte" onPress={() => Alert.alert('Ayuda', 'Para soporte escríbenos a soporte@ironmantrainer.app')} />
        <TouchableOpacity style={[styles.settingRow, { marginTop: 8 }]} onPress={handleSignOut}>
          <Ionicons name="log-out-outline" size={20} color="#EF4444" />
          <Text style={[styles.settingLabel, { color: '#EF4444' }]}>Cerrar sesión</Text>
          <View />
        </TouchableOpacity>
      </View>

      {/* Modal tipo carrera */}
      <Modal visible={editModal === 'race_type'} transparent animationType="slide">
        <View style={styles.modalOverlay}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>Tipo de distancia</Text>
            {RACE_TYPES.map(r => (
              <TouchableOpacity
                key={r.key}
                style={[styles.modalOption, tempRaceType === r.key && { borderColor: r.color, borderWidth: 2 }]}
                onPress={() => setTempRaceType(r.key)}
              >
                <Text style={styles.optionEmoji}>{r.icon}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={[styles.optionLabel, tempRaceType === r.key && { color: r.color }]}>{r.label}</Text>
                  <Text style={styles.optionDesc}>{r.desc}</Text>
                </View>
                {tempRaceType === r.key && <Ionicons name="checkmark-circle" size={22} color={r.color} />}
              </TouchableOpacity>
            ))}
            <View style={styles.modalButtons}>
              <TouchableOpacity style={styles.modalCancel} onPress={() => setEditModal(null)}>
                <Text style={styles.modalCancelText}>Cancelar</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.modalSave}
                onPress={() => saveChanges('race_type')}
              >
                <Text style={styles.modalSaveText}>Guardar</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>

      {/* Modal umbral */}
      <Modal visible={thresholdModal !== null} transparent animationType="slide" onRequestClose={() => setThresholdModal(null)}>
        <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : 'height'} style={{ flex: 1 }}>
          <View style={styles.modalOverlay}>
            <View style={styles.modalSheet}>
              <Text style={styles.modalTitle}>{thresholdModal?.label}</Text>
              <View style={styles.thresholdInputRow}>
                <TextInput
                  style={styles.thresholdInput}
                  value={thresholdInput}
                  onChangeText={setThresholdInput}
                  keyboardType="numeric"
                  placeholder="0"
                  placeholderTextColor={colors.textSecondary}
                  autoFocus
                  returnKeyType="done"
                  onSubmitEditing={saveThreshold}
                />
                <Text style={styles.thresholdInputUnit}>{thresholdModal?.unit}</Text>
              </View>
              <View style={styles.modalButtons}>
                <TouchableOpacity style={styles.modalCancel} onPress={() => setThresholdModal(null)}>
                  <Text style={styles.modalCancelText}>Cancelar</Text>
                </TouchableOpacity>
                <TouchableOpacity style={styles.modalSave} onPress={saveThreshold}>
                  <Text style={styles.modalSaveText}>Guardar</Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        </KeyboardAvoidingView>
      </Modal>

      {/* Modal fecha */}
      <Modal visible={editModal === 'race_date'} transparent animationType="slide">
        <View style={styles.modalOverlay}>
          <View style={styles.modalSheet}>
            <Text style={styles.modalTitle}>Fecha de carrera</Text>
            <Text style={styles.modalDateDisplay}>
              {tempDate.toLocaleDateString('es-ES', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })}
            </Text>
            <DateTimePicker
              value={tempDate}
              mode="date"
              display={Platform.OS === 'ios' ? 'spinner' : 'default'}
              minimumDate={new Date(Date.now() + 1000 * 60 * 60 * 24 * 14)}
              maximumDate={new Date(Date.now() + 1000 * 60 * 60 * 24 * 730)}
              onChange={(_, date) => { if (date) setTempDate(date); }}
              themeVariant="dark"
              style={{ width: '100%' }}
            />
            <View style={styles.modalButtons}>
              <TouchableOpacity style={styles.modalCancel} onPress={() => setEditModal(null)}>
                <Text style={styles.modalCancelText}>Cancelar</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={styles.modalSave}
                onPress={() => saveChanges('race_date')}
              >
                <Text style={styles.modalSaveText}>Guardar</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </ScrollView>
  );
}

function ThresholdCard({ icon, color, label, value, unit, onPress }: any) {
  return (
    <TouchableOpacity style={styles.thresholdCard} onPress={onPress}>
      <Ionicons name={icon} size={20} color={color} />
      <Text style={styles.thresholdValue}>
        {value === '—'
          ? <Text style={styles.thresholdHint}>Añadir</Text>
          : <>{value}<Text style={styles.thresholdUnit}> {unit}</Text></>}
      </Text>
      <Text style={styles.thresholdLabel}>{label}</Text>
    </TouchableOpacity>
  );
}

function SettingRow({ icon, label, onPress }: any) {
  return (
    <TouchableOpacity style={styles.settingRow} onPress={onPress}>
      <Ionicons name={icon} size={20} color={colors.textSecondary} />
      <Text style={styles.settingLabel}>{label}</Text>
      <Ionicons name="chevron-forward" size={16} color={colors.textSecondary} />
    </TouchableOpacity>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  content: { padding: 20, paddingBottom: 50 },
  title: { fontSize: 26, fontWeight: '700', color: colors.text, marginBottom: 24 },
  avatarSection: { alignItems: 'center', marginBottom: 32 },
  avatar: { width: 80, height: 80, borderRadius: 40, backgroundColor: '#EF4444', alignItems: 'center', justifyContent: 'center', marginBottom: 12 },
  avatarText: { fontSize: 32, fontWeight: '800', color: '#fff' },
  name: { fontSize: 20, fontWeight: '700', color: colors.text },
  email: { fontSize: 14, color: colors.textSecondary, marginTop: 4 },
  levelBadge: { marginTop: 10, backgroundColor: '#EF444420', paddingHorizontal: 14, paddingVertical: 5, borderRadius: 20 },
  levelText: { fontSize: 12, fontWeight: '700', color: '#EF4444', letterSpacing: 1 },
  section: { marginBottom: 28 },
  sectionTitle: { fontSize: 16, fontWeight: '700', color: colors.text, marginBottom: 12 },
  raceCard: { backgroundColor: '#1C1C1E', borderRadius: 14, overflow: 'hidden' },
  raceRow: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 16 },
  raceEmoji: { fontSize: 24 },
  raceInfo: { flex: 1 },
  raceLabel: { fontSize: 15, fontWeight: '700' },
  raceDesc: { fontSize: 13, color: colors.textSecondary, marginTop: 2 },
  raceDateLabel: { fontSize: 12, color: colors.textSecondary },
  raceDateValue: { fontSize: 15, fontWeight: '600', color: colors.text, marginTop: 2, textTransform: 'capitalize' },
  daysChip: { backgroundColor: '#EF444420', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12, marginRight: 4 },
  daysText: { fontSize: 13, fontWeight: '700', color: '#EF4444' },
  divider: { height: 1, backgroundColor: '#2C2C2E', marginHorizontal: 16 },
  thresholdsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  thresholdCard: { flexBasis: '47%', flexGrow: 1, backgroundColor: '#1C1C1E', borderRadius: 14, padding: 14, gap: 4 },
  thresholdValue: { fontSize: 20, fontWeight: '800', color: colors.text, marginTop: 4 },
  thresholdHint: { fontSize: 14, color: '#EF4444', fontWeight: '600' },
  thresholdUnit: { fontSize: 12, color: colors.textSecondary, fontWeight: '400' },
  thresholdLabel: { fontSize: 11, color: colors.textSecondary },
  connectRow: { flexDirection: 'row', alignItems: 'center', gap: 14, backgroundColor: '#1C1C1E', borderRadius: 14, padding: 14, marginBottom: 8 },
  connectIcon: { width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center' },
  connectInfo: { flex: 1 },
  connectName: { fontSize: 15, fontWeight: '600', color: colors.text },
  connectStatus: { fontSize: 12, color: colors.textSecondary, marginTop: 2 },
  connectButton: { backgroundColor: '#FC4C02', paddingHorizontal: 14, paddingVertical: 7, borderRadius: 10 },
  connectButtonDisabled: { backgroundColor: '#2C2C2E' },
  connectButtonText: { fontSize: 13, fontWeight: '700', color: '#fff' },
  connectButtonTextDisabled: { fontSize: 13, fontWeight: '600', color: colors.textSecondary },
  settingRow: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: '#1C1C1E', justifyContent: 'space-between' },
  settingLabel: { flex: 1, fontSize: 15, color: colors.text },
  modalOverlay: { flex: 1, backgroundColor: '#000000AA', justifyContent: 'flex-end' },
  modalSheet: { backgroundColor: '#1C1C1E', borderTopLeftRadius: 24, borderTopRightRadius: 24, padding: 24, paddingBottom: 40, gap: 12 },
  modalTitle: { fontSize: 18, fontWeight: '700', color: colors.text, marginBottom: 4 },
  modalDateDisplay: { fontSize: 16, fontWeight: '600', color: '#EF4444', textAlign: 'center', textTransform: 'capitalize', marginBottom: 4 },
  modalOption: { flexDirection: 'row', alignItems: 'center', gap: 12, backgroundColor: '#2C2C2E', borderRadius: 12, padding: 14, borderWidth: 2, borderColor: 'transparent' },
  optionEmoji: { fontSize: 24 },
  optionLabel: { fontSize: 15, fontWeight: '700', color: colors.text },
  optionDesc: { fontSize: 12, color: colors.textSecondary, marginTop: 2 },
  modalButtons: { flexDirection: 'row', gap: 12, marginTop: 8 },
  modalCancel: { flex: 1, paddingVertical: 14, borderRadius: 12, backgroundColor: '#2C2C2E', alignItems: 'center' },
  modalCancelText: { fontSize: 15, fontWeight: '600', color: colors.textSecondary },
  modalSave: { flex: 1, paddingVertical: 14, borderRadius: 12, backgroundColor: '#EF4444', alignItems: 'center' },
  modalSaveText: { fontSize: 15, fontWeight: '700', color: '#fff' },
  loaderScreen: { flex: 1, backgroundColor: colors.background, alignItems: 'center', justifyContent: 'center', gap: 16 },
  loaderText: { fontSize: 16, color: colors.textSecondary, fontWeight: '500' },
  thresholdInputRow: { flexDirection: 'row', alignItems: 'center', gap: 12, marginVertical: 12 },
  thresholdInput: { flex: 1, backgroundColor: '#2C2C2E', borderRadius: 12, padding: 16, fontSize: 28, fontWeight: '800', color: colors.text, textAlign: 'center' },
  thresholdInputUnit: { fontSize: 18, fontWeight: '600', color: colors.textSecondary, minWidth: 40 },
});
