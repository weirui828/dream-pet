from datetime import timedelta

from dreampet.memory import clustering, consolidation
from dreampet.memory.repo import Memory
from dreampet.memory.retrieval import retrieve


def _add(ctx, text, title=None, kind="episodic", **kw):
    vec = ctx.providers.embeddings.embed_one(text)
    m = Memory(id=ctx.new_id("m"), pet_id=ctx.pet_id, kind=kind, content=text, title=title, created_at=ctx.now(),
               embedding=vec, embed_model=ctx.providers.embeddings.model_name, **kw)
    ctx.repo.add_memory(m)
    return m


def test_fake_embeddings_are_semantic(ctx):
    e = ctx.providers.embeddings
    a, b, c = e.embed(["black holes swallow light near the event horizon",
                       "light cannot escape a black hole event horizon",
                       "bread dough rises because yeast ferments sugar"])
    assert clustering.cos_dist(a, b) < clustering.cos_dist(a, c)


def test_knn_and_retrieval(ctx):
    near = _add(ctx, "Tardigrades survive the vacuum of space", "Tardigrade", importance=0.9)
    _add(ctx, "Roman aqueducts carried water across valleys", "Aqueduct")
    hits = retrieve(ctx, "can tardigrades live in space", k=1)
    assert hits[0][0].id == near.id
    assert ctx.repo.get_memory(near.id).access_count == 1


def test_seed_clusters_and_assign(ctx):
    labels = {c.label for c in ctx.repo.list_clusters(ctx.pet_id)}
    assert "black holes" in labels  # seeded from the persona's interests
    v = ctx.providers.embeddings.embed_one("black holes and their event horizons")
    c = clustering.assign(ctx, v, "Event horizon")
    assert c.label == "black holes"
    far = ctx.providers.embeddings.embed_one("sourdough starter fermentation")
    c2 = clustering.assign(ctx, far, "Sourdough")
    assert c2.label == "Sourdough" and not c2.seed


def test_recluster_keeps_ids_and_counts(ctx):
    for t in ("black hole event horizon light", "black holes evaporate hawking radiation",
              "sourdough bread fermentation yeast", "bread baking yeast dough"):
        m = _add(ctx, t, t.split()[0])
        c = clustering.assign(ctx, m.embedding, m.title)
        ctx.repo.update_memory(m.id, cluster_id=c.id)
    before = {c.id for c in ctx.repo.list_clusters(ctx.pet_id) if c.seed}
    stats = clustering.recluster(ctx)
    after = {c.id for c in ctx.repo.list_clusters(ctx.pet_id)}
    assert before <= after  # seed clusters survive
    assert stats["clusters"] >= 2
    assert clustering.topic_entropy(ctx, ctx.pet_id) > 0


def test_merge_and_decay(ctx):
    a = _add(ctx, "Octopuses have three hearts and blue blood", "Octopus")
    ctx.clock.advance(timedelta(minutes=5))
    b = _add(ctx, "Octopuses have three hearts and blue blood", "Octopus again")
    merged = consolidation.merge_duplicates(ctx, [a, b])
    assert merged == [(a.id, b.id)]
    assert ctx.repo.get_memory(a.id).strength == 2.0
    assert ctx.repo.get_memory(b.id).archived
    ctx.clock.advance(timedelta(days=1))
    weak = _add(ctx, "a fleeting thought about clouds", strength=0.21)
    dreamed = _add(ctx, "a dreamed-about thing", strength=0.21)
    ctx.repo.add_link("dream-mem", dreamed.id, "dream_used")
    ctx.clock.advance(timedelta(hours=1))
    archived = consolidation.decay(ctx, since=ctx.now())
    assert weak.id in archived
    assert dreamed.id not in archived  # dreamed memories resist decay
