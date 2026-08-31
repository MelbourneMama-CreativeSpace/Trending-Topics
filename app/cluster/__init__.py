"""Deduplication and topic clustering (PRD 24, 25, 26)."""

from app.cluster.clusterer import Cluster, cluster_articles, clusters_to_topics, make_topic_id
from app.cluster.dedup import DedupReport, deduplicate
from app.cluster.similarity import (
    IdfModel,
    are_near_duplicate_titles,
    build_idf,
    cosine,
    same_story,
    shared_distinctive,
    title_ratio,
)
from app.cluster.text import jaccard, token_set, tokenize
from app.cluster.topics import ReconcileReport, merge_topic_history, reconcile

__all__ = [
    "Cluster",
    "DedupReport",
    "IdfModel",
    "ReconcileReport",
    "are_near_duplicate_titles",
    "build_idf",
    "cluster_articles",
    "clusters_to_topics",
    "cosine",
    "deduplicate",
    "jaccard",
    "make_topic_id",
    "merge_topic_history",
    "reconcile",
    "same_story",
    "shared_distinctive",
    "title_ratio",
    "token_set",
    "tokenize",
]
