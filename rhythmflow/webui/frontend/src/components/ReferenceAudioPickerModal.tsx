import { InfoCircleOutlined, ReloadOutlined } from '@ant-design/icons';
import { App, Button, Checkbox, Input, Modal, Space, Table, Tabs, Tag, Tooltip, Typography } from 'antd';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { t } from '../i18n';
import { useStore } from '../store';
import type { ReferenceDifficulty, ReferenceGame, ReferenceSong } from '../types';

interface Props {
  open: boolean;
  onClose: () => void;
}

const GAMES: ReferenceGame[] = ['maimai', 'chunithm'];

const DIFFICULTY_TAG_STYLES: Record<string, { backgroundColor: string; borderColor: string; color: string }> = {
  BASIC: { backgroundColor: '#52c41a', borderColor: '#52c41a', color: '#ffffff' },
  ADVANCED: { backgroundColor: '#fadb14', borderColor: '#fadb14', color: '#111827' },
  EXPERT: { backgroundColor: '#ff4d4f', borderColor: '#ff4d4f', color: '#ffffff' },
  MASTER: { backgroundColor: '#722ed1', borderColor: '#722ed1', color: '#ffffff' },
  'Re:MASTER': { backgroundColor: '#ffffff', borderColor: '#d9d9d9', color: '#111827' },
  ULTIMA: { backgroundColor: '#000000', borderColor: '#434343', color: '#ffffff' },
  "WORLD'S END": { backgroundColor: '#000000', borderColor: '#434343', color: '#ffffff' },
};

function gameLabel(lang: 'zh' | 'en', game: ReferenceGame): string {
  return t(lang, game === 'maimai' ? 'reference_audio_tab_maimai' : 'reference_audio_tab_chunithm');
}

function difficultyTagStyle(label: string) {
  return DIFFICULTY_TAG_STYLES[label] ?? DIFFICULTY_TAG_STYLES.MASTER;
}

function difficultyText(difficulty: ReferenceDifficulty): string {
  return [difficulty.label, difficulty.level].filter(Boolean).join(' ');
}

function formatUpdatedAt(lang: 'zh' | 'en', iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (value: number) => String(value).padStart(2, '0');
  const month = pad(date.getMonth() + 1);
  const day = pad(date.getDate());
  const hours = pad(date.getHours());
  const minutes = pad(date.getMinutes());
  return lang === 'zh' ? `${month}月${day}日 ${hours}:${minutes}` : `${month}/${day} ${hours}:${minutes}`;
}

function DifficultyTags({ song }: { song: ReferenceSong }): JSX.Element {
  if (!song.difficulties.length) {
    return <Typography.Text type="secondary">{song.difficulty_summary || '-'}</Typography.Text>;
  }
  return (
    <Space size={[4, 4]} wrap>
      {song.difficulties.map((difficulty) => (
        <Tag
          key={`${difficulty.label}:${difficulty.level}:${difficulty.index ?? 'x'}`}
          style={{ marginInlineEnd: 0, ...difficultyTagStyle(difficulty.label) }}
        >
          {difficultyText(difficulty)}
        </Tag>
      ))}
    </Space>
  );
}

function errorMessage(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '';
}

export function ReferenceAudioPickerModal({ open, onClose }: Props): JSX.Element {
  const store = useStore();
  const lang = store.language;
  const { message } = App.useApp();
  const [activeGame, setActiveGame] = useState<ReferenceGame>('maimai');
  const [persist, setPersist] = useState(false);
  const [queries, setQueries] = useState<Record<ReferenceGame, string>>({ maimai: '', chunithm: '' });
  const [songs, setSongs] = useState<Record<ReferenceGame, ReferenceSong[]>>({ maimai: [], chunithm: [] });
  const [updatedAt, setUpdatedAt] = useState<Record<ReferenceGame, string | null>>({ maimai: null, chunithm: null });
  const [loaded, setLoaded] = useState<Record<ReferenceGame, boolean>>({ maimai: false, chunithm: false });
  const [loadingGame, setLoadingGame] = useState<ReferenceGame | null>(null);
  const [downloadingKey, setDownloadingKey] = useState<string | null>(null);

  const loadSongs = useCallback(
    async (game: ReferenceGame) => {
      setLoadingGame(game);
      try {
        const result = await store.searchReferenceSongs(game, '');
        setSongs((current) => ({ ...current, [game]: result.songs }));
        setUpdatedAt((current) => ({ ...current, [game]: result.updated_at }));
        setLoaded((current) => ({ ...current, [game]: true }));
      } catch (err) {
        console.error('Could not load LXNS songs', err);
        message.error(t(lang, 'reference_audio_download_failed'));
      } finally {
        setLoadingGame(null);
      }
    },
    [lang, message, store],
  );

  const refreshSongs = useCallback(
    async (game: ReferenceGame) => {
      setLoadingGame(game);
      try {
        const result = await store.refreshReferenceSongs(game);
        setSongs((current) => ({ ...current, [game]: result.songs }));
        setUpdatedAt((current) => ({ ...current, [game]: result.updated_at }));
        setLoaded((current) => ({ ...current, [game]: true }));
      } catch (err) {
        console.error('Could not refresh LXNS songs', err);
        message.error(t(lang, 'reference_audio_refresh_failed'));
      } finally {
        setLoadingGame(null);
      }
    },
    [lang, message, store],
  );

  useEffect(() => {
    if (open && !loaded[activeGame]) {
      void loadSongs(activeGame);
    }
  }, [activeGame, loadSongs, loaded, open]);

  const filteredSongs = useMemo(() => {
    const needle = queries[activeGame].trim().toLocaleLowerCase();
    const source = songs[activeGame];
    if (!needle) return source.slice(0, 100);
    return source
      .filter((song) =>
        [song.id, song.title, song.artist, song.version, song.genre, song.difficulty_summary]
          .join(' ')
          .toLocaleLowerCase()
          .includes(needle),
      )
      .slice(0, 100);
  }, [activeGame, queries, songs]);

  const useSong = useCallback(
    async (song: ReferenceSong) => {
      if (persist && !store.settings.output_dir.trim()) {
        message.warning(t(lang, 'reference_audio_persist_output_required'));
        return;
      }
      const key = `${activeGame}:${song.asset_song_id}`;
      setDownloadingKey(key);
      try {
        const path = await store.downloadReferenceAudio(
          activeGame,
          song.asset_song_id,
          song.title,
          persist,
        );
        store.setReference(path);
        message.success(t(lang, 'reference_audio_downloaded'));
        onClose();
      } catch (err) {
        console.error('Could not download LXNS reference audio', err);
        const detail = errorMessage(err);
        message.error(
          detail ? `${t(lang, 'reference_audio_download_failed')} ${detail}` : t(lang, 'reference_audio_download_failed'),
        );
      } finally {
        setDownloadingKey(null);
      }
    },
    [activeGame, lang, message, onClose, persist, store],
  );

  const columns = useMemo(
    () => [
      {
        title: t(lang, 'reference_song_title'),
        dataIndex: 'title',
        width: 260,
        ellipsis: true,
        render: (title: string, song: ReferenceSong) => (
          <Space direction="vertical" size={0}>
            <Typography.Text strong ellipsis style={{ maxWidth: 240 }}>
              {title}
            </Typography.Text>
            <Typography.Text type="secondary">#{song.id}</Typography.Text>
          </Space>
        ),
      },
      {
        title: t(lang, 'reference_song_artist'),
        dataIndex: 'artist',
        width: 180,
        ellipsis: true,
      },
      {
        title: t(lang, 'reference_song_version'),
        dataIndex: 'version',
        width: 150,
        ellipsis: true,
      },
      {
        title: t(lang, 'reference_song_difficulty'),
        dataIndex: 'difficulties',
        width: 260,
        render: (_difficulties: ReferenceDifficulty[], song: ReferenceSong) => (
          <DifficultyTags song={song} />
        ),
      },
      {
        title: '',
        key: 'action',
        width: 88,
        render: (_value: unknown, song: ReferenceSong) => {
          const key = `${activeGame}:${song.asset_song_id}`;
          return (
            <Button
              type="primary"
              size="small"
              loading={downloadingKey === key}
              disabled={store.busy || downloadingKey !== null}
              onClick={() => void useSong(song)}
            >
              {t(lang, 'reference_audio_use')}
            </Button>
          );
        },
      },
    ],
    [activeGame, downloadingKey, lang, store.busy, useSong],
  );

  const lastUpdated = updatedAt[activeGame];
  const lastUpdatedText = lastUpdated
    ? t(lang, 'reference_audio_last_updated', { time: formatUpdatedAt(lang, lastUpdated) })
    : t(lang, 'reference_audio_refresh');

  const title = (
    <Space size={8}>
      <span>{t(lang, 'reference_audio_library')}</span>
      <Tooltip title={lastUpdatedText}>
        <Button
          type="text"
          shape="circle"
          size="small"
          aria-label={t(lang, 'reference_audio_refresh')}
          loading={loadingGame === activeGame}
          onClick={() => void refreshSongs(activeGame)}
          icon={<ReloadOutlined />}
        />
      </Tooltip>
      <Tooltip title={t(lang, 'reference_audio_library_tip')}>
        <InfoCircleOutlined />
      </Tooltip>
    </Space>
  );

  return (
    <Modal title={title} open={open} onCancel={onClose} footer={null} width={860} destroyOnClose>
      <Tabs
        activeKey={activeGame}
        onChange={(key) => setActiveGame(key as ReferenceGame)}
        items={GAMES.map((game) => ({
          key: game,
          label: gameLabel(lang, game),
          children: (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Input.Search
                allowClear
                value={queries[game]}
                placeholder={t(lang, 'reference_audio_search')}
                onChange={(event) =>
                  setQueries((current) => ({ ...current, [game]: event.target.value }))
                }
              />
              <Table
                size="small"
                rowKey={(song) => `${game}:${song.id}:${song.asset_song_id}`}
                columns={columns}
                dataSource={game === activeGame ? filteredSongs : []}
                loading={loadingGame === game}
                pagination={{ pageSize: 8, size: 'small' }}
                scroll={{ x: 760, y: 320 }}
                locale={{
                  emptyText:
                    loadingGame === game
                      ? t(lang, 'reference_audio_loading')
                      : t(lang, 'reference_audio_empty'),
                }}
              />
            </Space>
          ),
        }))}
      />
      <Checkbox checked={persist} onChange={(event) => setPersist(event.target.checked)}>
        {t(lang, 'reference_audio_persist')}
      </Checkbox>
    </Modal>
  );
}
