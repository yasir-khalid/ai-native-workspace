import type { Chapter, Paragraph, Sentence } from "./calls";

/** Keep the last spoken sentence selected through timing gaps instead of
 * clearing/re-adding the highlight and listening status between utterances. */
export function sentenceAtTime(sentences: Sentence[], time: number): Sentence | null {
  if (!Number.isFinite(time)) return null;
  let low = 0, high = sentences.length - 1, found = -1;
  while (low <= high) {
    const middle = (low + high) >>> 1;
    if (sentences[middle].start <= time) { found = middle; low = middle + 1; }
    else high = middle - 1;
  }
  return found < 0 ? null : sentences[found];
}

export type TranscriptBlock = {
  id: string;
  speaker: string;
  start: number;
  end: number;
  chapterId: string | null;
  sentences: Sentence[];
  continued: boolean;
};

/** Presentation boundaries only: transcript text, sentence IDs and timing remain untouched. */
export function transcriptBlocks(paragraphs: Paragraph[], chapters: Chapter[], lines = false): TranscriptBlock[] {
  const ordered = [...chapters].sort((a, b) => a.start - b.start);
  const blocks: TranscriptBlock[] = [];
  for (const paragraph of paragraphs) {
    let block: TranscriptBlock | null = null;
    let words = 0;
    for (const sentence of paragraph.sentences) {
      const chapterId = ordered.findLast((chapter) => chapter.start <= sentence.start)?.id ?? null;
      const length = sentence.text.trim().split(/\s+/).filter(Boolean).length;
      if (!block || lines || words + length > 105 || sentence.start - block.start >= 40 || block.chapterId !== chapterId) {
        block = {
          id: sentence.id, speaker: paragraph.speaker, start: sentence.start, end: sentence.end,
          chapterId, sentences: [], continued: blocks.at(-1)?.speaker === paragraph.speaker,
        };
        blocks.push(block);
        words = 0;
      }
      block.sentences.push(sentence);
      block.end = sentence.end;
      words += length;
    }
  }
  return blocks;
}
