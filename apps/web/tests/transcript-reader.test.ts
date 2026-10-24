import { test } from "node:test";
import assert from "node:assert/strict";
import { sentenceAtTime, transcriptBlocks } from "../lib/transcript-reader";
import type { Chapter, Paragraph, Sentence } from "../lib/calls";

const sentences: Sentence[] = [
  { id: "s1", paragraph_id: "p1", text: "First passage.", start: 2, end: 4 },
  { id: "s2", paragraph_id: "p1", text: "Second passage.", start: 7, end: 9 },
  { id: "s3", paragraph_id: "p1", text: "Third passage.", start: 12, end: 14 },
];

test("playback selection remains stable in timing gaps and handles seeks in both directions", () => {
  assert.equal(sentenceAtTime(sentences, 0), null);
  assert.equal(sentenceAtTime(sentences, 2)?.id, "s1");
  assert.equal(sentenceAtTime(sentences, 5)?.id, "s1");
  assert.equal(sentenceAtTime(sentences, 7)?.id, "s2");
  assert.equal(sentenceAtTime(sentences, 20)?.id, "s3");
  assert.equal(sentenceAtTime(sentences, 3)?.id, "s1");
  assert.equal(sentenceAtTime(sentences, Number.NaN), null);
  assert.equal(sentenceAtTime([], 3), null);
});

test("reader and line layouts preserve source sentence IDs, text, speakers and citation timing", () => {
  const paragraphs: Paragraph[] = [{ id: "p1", speaker: "Speaker A", start: 2, end: 14, sentences }];
  const chapters = [{ id: "c1", title: "Opening", start: 0 }, { id: "c2", title: "Discussion", start: 10 }] as Chapter[];
  for (const lines of [false, true]) {
    const blocks = transcriptBlocks(paragraphs, chapters, lines);
    assert.deepEqual(blocks.flatMap(block => block.sentences), sentences);
    assert.ok(blocks.every(block => block.speaker === "Speaker A"));
    assert.equal(blocks.at(-1)?.chapterId, "c2");
    assert.equal(blocks.at(-1)?.start, 12);
    assert.equal(blocks.at(-1)?.end, 14);
  }
});
