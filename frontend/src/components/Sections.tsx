// Seções da resposta que vêm das funções chamadas pelo Gemini (PokéAPI e TCGdex)
import { Image, ScrollView, StyleSheet, Text, View } from 'react-native';
import type { EvolutionStage, PokedexDescription, TcgCards, ToolCall, Weaknesses } from '../api';
import { colors, fonts, typeColors, typeNames } from '../theme';

export const sourceColors: Record<string, string> = {
  'Banco local': colors.ink,
  'PokéAPI': '#2A75BB',
  'TCGdex': '#B07A00',
};

const sourceColorsDark: Record<string, string> = {
  'Banco local': '#CFE3D4',
  'PokéAPI': '#7DB8F0',
  'TCGdex': '#F2C14E',
};

export function SourceTag({ source, dark }: { source: string; dark?: boolean }) {
  const color = (dark ? sourceColorsDark : sourceColors)[source] || colors.slate;
  return (
    <View style={[styles.source, { borderColor: color }]}>
      <Text style={[styles.sourceText, { color }]}>{source}</Text>
    </View>
  );
}

function SectionTitle({ title, source }: { title: string; source: string }) {
  return (
    <View style={styles.titleRow}>
      <Text style={styles.title}>{title}</Text>
      <SourceTag source={source} />
    </View>
  );
}

export function DescriptionSection({ d }: { d: PokedexDescription }) {
  if (!d.text) return null;
  return (
    <View style={styles.box}>
      <SectionTitle title={`Pokédex: ${d.pokemon}`} source="PokéAPI" />
      {!!d.genus && <Text style={styles.genus}>{d.genus}</Text>}
      <Text style={styles.quote}>“{d.text}”</Text>
      <Text style={styles.meta}>
        Texto original em {d.language === 'en' ? 'inglês' : 'espanhol'}{d.version ? `, versão ${d.version}` : ''}
      </Text>
    </View>
  );
}

function fmt(m: number) {
  return `×${String(m).replace('.', ',')}`;
}

function TypeMult({ type, mult }: { type: string; mult?: number }) {
  return (
    <View style={[styles.mult, { backgroundColor: typeColors[type] || colors.slate }]}>
      <Text style={styles.multText}>{typeNames[type] || type}{mult !== undefined ? `  ${fmt(mult)}` : ''}</Text>
    </View>
  );
}

export function WeaknessSection({ w }: { w: Weaknesses }) {
  const who = w.pokemon || w.types.map((t) => typeNames[t] || t).join('/');
  const groups: [string, [string, number | undefined][]][] = [
    ['Fraco contra', Object.entries(w.weak)],
    ['Resiste a', Object.entries(w.resist)],
    ['Imune a', w.immune.map((t) => [t, undefined])],
  ];
  return (
    <View style={styles.box}>
      <SectionTitle title={`Fraquezas e resistências: ${who}`} source="PokéAPI" />
      {groups.filter(([, items]) => items.length > 0).map(([label, items]) => (
        <View key={label} style={styles.multGroup}>
          <Text style={styles.groupLabel}>{label}</Text>
          <View style={styles.wrap}>
            {items.map(([t, m]) => <TypeMult key={t} type={t} mult={m} />)}
          </View>
        </View>
      ))}
    </View>
  );
}

export function EvolutionSection({ stages }: { stages: EvolutionStage[] }) {
  const depths = Array.from(new Set(stages.map((s) => s.stage))).sort();
  const nameOf = (species: string | null) => stages.find((s) => s.species === species)?.name || species;
  return (
    <View style={styles.box}>
      <SectionTitle title="Linha evolutiva" source="PokéAPI" />
      {depths.map((d) => (
        <View key={d} style={styles.evoRow}>
          <Text style={styles.groupLabel}>Estágio {d}</Text>
          <View style={styles.wrap}>
            {stages.filter((s) => s.stage === d).map((s) => (
              <View key={s.species} style={styles.evo}>
                {s.sprite_url ? <Image source={{ uri: s.sprite_url }} style={styles.evoSprite} resizeMode="contain" /> : <View style={styles.evoSprite} />}
                <View style={{ flexShrink: 1 }}>
                  <Text style={styles.evoName}>{s.name}</Text>
                  {!!s.from && <Text style={styles.evoCond}>de {nameOf(s.from)}: {s.condition || 'condição especial'}</Text>}
                </View>
              </View>
            ))}
          </View>
        </View>
      ))}
    </View>
  );
}

export function TcgSection({ c }: { c: TcgCards }) {
  if (!c.cards.length) {
    return (
      <View style={styles.box}>
        <SectionTitle title={`Cartas TCG: ${c.pokemon}`} source="TCGdex" />
        <Text style={styles.meta}>Nenhuma carta com imagem foi encontrada.</Text>
      </View>
    );
  }
  return (
    <View style={styles.box}>
      <SectionTitle title={`Cartas TCG: ${c.pokemon}`} source="TCGdex" />
      <Text style={styles.meta}>Mostrando {c.cards.length} de {c.total} cartas encontradas, das mais recentes.</Text>
      <ScrollView horizontal contentContainerStyle={{ gap: 10, paddingTop: 8 }}>
        {c.cards.map((card) => (
          <View key={card.id} style={{ width: 132 }}>
            <Image source={{ uri: card.imagem }} style={styles.card} resizeMode="contain" accessibilityLabel={card.nome} />
            <Text style={styles.cardName} numberOfLines={1}>{card.nome}</Text>
            <Text style={styles.meta}>{card.id}</Text>
          </View>
        ))}
      </ScrollView>
    </View>
  );
}

const fnLabels: Record<string, string> = {
  consultar_banco: 'consultar_banco',
  descricao_pokedex: 'descricao_pokedex',
  linha_evolutiva: 'linha_evolutiva',
  fraquezas_e_resistencias: 'fraquezas_e_resistencias',
  cartas_tcg: 'cartas_tcg',
};

function argsText(name: string, args: Record<string, unknown>) {
  if (name === 'consultar_banco') return String(args.explicacao || '');
  return Object.entries(args).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : String(v)}`).join(', ');
}

export function ToolTrace({ calls }: { calls: ToolCall[] }) {
  return (
    <View style={{ gap: 8 }}>
      {calls.map((c, i) => (
        <View key={i} style={styles.call}>
          <Text style={styles.callIndex}>{i + 1}</Text>
          <View style={{ flex: 1, gap: 2 }}>
            <View style={styles.callHead}>
              <Text style={styles.callName}>{fnLabels[c.name] || c.name}</Text>
              <SourceTag source={c.source} dark />
            </View>
            {!!argsText(c.name, c.args) && <Text style={styles.callArgs}>{argsText(c.name, c.args)}</Text>}
            <Text style={[styles.callResult, !c.ok && { color: '#FF9AA8' }]}>
              {c.ok ? '✓' : '✗'} {c.summary} · {c.ms} ms
            </Text>
          </View>
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  box: { backgroundColor: colors.card, borderRadius: 14, borderWidth: 1, borderColor: colors.line, padding: 14, gap: 6 },
  titleRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' },
  title: { fontFamily: fonts.displayMedium, fontSize: 16, color: colors.ink, flexShrink: 1 },
  source: { borderWidth: 1, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 1 },
  sourceText: { fontFamily: fonts.bodyBold, fontSize: 11 },
  genus: { fontFamily: fonts.bodyMedium, fontSize: 13, color: colors.slate },
  quote: { fontFamily: fonts.body, fontSize: 15, lineHeight: 22, color: colors.ink },
  meta: { fontFamily: fonts.body, fontSize: 12, color: colors.slate },
  wrap: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  multGroup: { gap: 4, marginTop: 4 },
  groupLabel: { fontFamily: fonts.bodyMedium, fontSize: 13, color: colors.slate },
  mult: { paddingHorizontal: 9, paddingVertical: 3, borderRadius: 999 },
  multText: { fontFamily: fonts.bodyBold, fontSize: 12, color: '#fff' },
  evoRow: { gap: 4, marginTop: 4 },
  evo: { flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: colors.paper, borderRadius: 10, padding: 6, paddingRight: 12, maxWidth: 260 },
  evoSprite: { width: 48, height: 48 },
  evoName: { fontFamily: fonts.displayMedium, fontSize: 14, color: colors.ink },
  evoCond: { fontFamily: fonts.body, fontSize: 12, color: colors.slate },
  card: { width: 132, height: 182, borderRadius: 8, backgroundColor: colors.paper },
  cardName: { fontFamily: fonts.bodyMedium, fontSize: 13, color: colors.ink, marginTop: 4 },
  call: { flexDirection: 'row', gap: 10 },
  callIndex: { fontFamily: fonts.display, fontSize: 14, color: '#93A4B8', width: 16 },
  callHead: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  callName: { fontFamily: fonts.mono, fontSize: 13, color: colors.screenText },
  callArgs: { fontFamily: fonts.body, fontSize: 12, color: '#C9D3DE' },
  callResult: { fontFamily: fonts.body, fontSize: 12, color: '#93A4B8' },
});
