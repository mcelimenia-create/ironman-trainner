import React, { useState } from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { colors } from '../lib/theme';
import type { GymBlock, Exercise, ExerciseCategory } from '../types';

const CATEGORY_CONFIG: Record<ExerciseCategory, { color: string; icon: string }> = {
  core:     { color: '#F97316', icon: 'radio-button-on' },
  legs:     { color: '#EF4444', icon: 'walk' },
  upper:    { color: '#3B82F6', icon: 'barbell' },
  hip:      { color: '#8B5CF6', icon: 'body' },
  mobility: { color: '#22C55E', icon: 'leaf' },
};

interface Props {
  blocks: GymBlock[];
}

export default function GymSessionView({ blocks }: Props) {
  const [completedExercises, setCompletedExercises] = useState<Set<string>>(new Set());

  const toggleExercise = (key: string) => {
    setCompletedExercises(prev => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  return (
    <View style={styles.container}>
      {blocks.map((block, bi) => (
        <View key={bi} style={styles.block}>
          <Text style={styles.blockTitle}>{block.title}</Text>
          {block.exercises.map((ex, ei) => {
            const key = `${bi}-${ei}`;
            const done = completedExercises.has(key);
            const cat = CATEGORY_CONFIG[ex.category];
            return (
              <TouchableOpacity
                key={key}
                style={[styles.exerciseRow, done && styles.exerciseRowDone]}
                onPress={() => toggleExercise(key)}
                activeOpacity={0.7}
              >
                <View style={[styles.catDot, { backgroundColor: cat.color }]} />
                <View style={styles.exerciseInfo}>
                  <Text style={[styles.exerciseName, done && styles.textDone]}>{ex.name}</Text>
                  <View style={styles.exerciseMeta}>
                    <MetaChip icon="repeat" value={`${ex.sets} x ${ex.reps}`} />
                    {ex.rest_sec > 0 && (
                      <MetaChip icon="timer-outline" value={ex.rest_sec >= 60 ? `${ex.rest_sec / 60}' desc` : `${ex.rest_sec}" desc`} />
                    )}
                  </View>
                  {ex.notes && !done && (
                    <Text style={styles.exerciseNotes}>💡 {ex.notes}</Text>
                  )}
                </View>
                <View style={[styles.checkCircle, done && styles.checkCircleDone]}>
                  {done && <Ionicons name="checkmark" size={14} color="#fff" />}
                </View>
              </TouchableOpacity>
            );
          })}
        </View>
      ))}
    </View>
  );
}

function MetaChip({ icon, value }: { icon: string; value: string }) {
  return (
    <View style={styles.metaChip}>
      <Ionicons name={icon as any} size={11} color={colors.textSecondary} />
      <Text style={styles.metaText}>{value}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  container: { gap: 16 },
  block: {
    backgroundColor: '#1C1C1E', borderRadius: 14, padding: 14,
  },
  blockTitle: {
    fontSize: 14, fontWeight: '700', color: colors.text,
    marginBottom: 12, borderBottomWidth: 1,
    borderBottomColor: '#2C2C2E', paddingBottom: 8,
  },
  exerciseRow: {
    flexDirection: 'row', alignItems: 'flex-start', gap: 10,
    paddingVertical: 10, borderBottomWidth: 1, borderBottomColor: '#2C2C2E',
  },
  exerciseRowDone: { opacity: 0.45 },
  catDot: { width: 4, borderRadius: 2, minHeight: 36, marginTop: 2 },
  exerciseInfo: { flex: 1, gap: 4 },
  exerciseName: { fontSize: 14, fontWeight: '600', color: colors.text },
  textDone: { textDecorationLine: 'line-through', color: colors.textSecondary },
  exerciseMeta: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },
  metaChip: {
    flexDirection: 'row', alignItems: 'center', gap: 3,
    backgroundColor: '#2C2C2E', paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8,
  },
  metaText: { fontSize: 11, color: colors.textSecondary },
  exerciseNotes: { fontSize: 12, color: '#F59E0B', fontStyle: 'italic', marginTop: 2 },
  checkCircle: {
    width: 24, height: 24, borderRadius: 12,
    borderWidth: 2, borderColor: '#3C3C3E',
    alignItems: 'center', justifyContent: 'center',
  },
  checkCircleDone: { backgroundColor: '#22C55E', borderColor: '#22C55E' },
});
