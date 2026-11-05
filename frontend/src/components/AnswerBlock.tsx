import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { AskResponse, Pokemon } from '../api';
import { colors, fonts, regionNames, typeNames } from '../theme';
import PokemonCard from './PokemonCard';
import { DescriptionSection, EvolutionSection, SourceTag, TcgSection, ToolTrace, WeaknessSection } from './Sections';

const cell = (v: unknown) => {
  if (v == null) return '—';
  const t = String(v);
  return typeNames[t] || regionNames[t] || t;
};

type Props = { data: AskResponse; width: number; onOpen: (p: Pokemon) => void };

export default function AnswerBlock({ data, width, onOpen }: Props) {
  const [showTrace, setShowTrace] = useState(false);
  const columns = width >= 700 ? 3 : width >= 440 ? 2 : 1;
  const gap = 12;
  const cardWidth = Math.floor((width - gap * (columns - 1)) / columns);
  const sources = Array.from(new Set(data.tool_calls.filter((c) => c.ok).map((c) => c.source)));
  const n = data.tool_calls.length;

  return (
    <View style={styles.wrap}>
      <Text style={styles.answer}>{data.answer}</Text>
      {sources.length > 0 && (
        <View style={styles.sources}>
          <Text style={styles.meta}>Fontes:</Text>
          {sources.map((s) => <SourceTag key={s} source={s} />)}
        </View>
      )}

      {data.description && <DescriptionSection d={data.description} />}
      {data.weaknesses && <WeaknessSection w={data.weaknesses} />}
      {data.evolution && data.evolution.length > 0 && <EvolutionSection stages={data.evolution} />}

      {data.pokemon.length > 0 && (
        <View style={[styles.grid, { gap }]}>
          {data.pokemon.map((p, i) => (
            <PokemonCard key={p.id} pokemon={p} rank={i + 1} width={cardWidth} onPress={() => onOpen(p)} />
          ))}
        </View>
      )}

      {data.cards && <TcgSection c={data.cards} />}

      {data.table && data.table.rows.length > 0 && (
        <ScrollView horizontal style={styles.table}>
          <View>
            <View style={[styles.tr, styles.thead]}>
              {data.table.columns.map((c) => <Text key={c} style={[styles.td, styles.th]}>{c}</Text>)}
            </View>
            {data.table.rows.map((r, i) => (
              <View key={i} style={styles.tr}>
                {data.table!.columns.map((c) => <Text key={c} style={styles.td}>{cell(r[c])}</Text>)}
              </View>
            ))}
          </View>
        </ScrollView>
      )}

      {data.truncated && <Text style={styles.meta}>Mostrando os primeiros {data.row_count} resultados.</Text>}

      <Pressable onPress={() => setShowTrace((s) => !s)} accessibilityRole="button" style={styles.toggle}>
        <Text style={styles.toggleText}>{showTrace ? 'Ocultar como a resposta foi montada' : 'Ver como a resposta foi montada'}</Text>
        <Text style={styles.meta}>
          {n} {n === 1 ? 'função chamada' : 'funções chamadas'} em {(data.elapsed_ms / 1000).toFixed(1)} s
          {data.cached ? ', do cache' : ''}
        </Text>
      </Pressable>

      {showTrace && (
        <View style={styles.screen}>
          <Text style={styles.screenNote}>
            O Gemini{data.model ? ` (${data.model})` : ''} escolheu estas funções. O backend executou cada uma e devolveu o resultado para ele.
          </Text>
          <ToolTrace calls={data.tool_calls} />
          {!!data.sql && (
            <View style={{ gap: 4 }}>
              <Text style={styles.screenNote}>SQL enviado em consultar_banco{data.explanation ? `: ${data.explanation}` : ''}</Text>
              <Text style={styles.sql} selectable>{data.sql}</Text>
            </View>
          )}
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 12 },
  answer: { fontFamily: fonts.body, fontSize: 16, lineHeight: 24, color: colors.ink, maxWidth: 640 },
  sources: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginTop: -4 },
  grid: { flexDirection: 'row', flexWrap: 'wrap' },
  table: { borderWidth: 1, borderColor: colors.line, borderRadius: 12, backgroundColor: colors.card },
  tr: { flexDirection: 'row', borderBottomWidth: 1, borderColor: colors.paper },
  thead: { backgroundColor: colors.paper },
  td: { fontFamily: fonts.body, fontSize: 14, color: colors.ink, paddingVertical: 8, paddingHorizontal: 12, minWidth: 120 },
  th: { fontFamily: fonts.bodyBold },
  toggle: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: 8 },
  toggleText: { fontFamily: fonts.bodyBold, fontSize: 14, color: colors.red, textDecorationLine: 'underline' },
  meta: { fontFamily: fonts.body, fontSize: 13, color: colors.slate },
  screen: { backgroundColor: colors.screen, borderRadius: 12, padding: 14, gap: 12 },
  sql: { fontFamily: fonts.mono, fontSize: 13, lineHeight: 20, color: colors.screenText },
  screenNote: { fontFamily: fonts.body, fontSize: 12, color: '#93A4B8' },
});
