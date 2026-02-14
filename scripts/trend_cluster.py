# trend_cluster.py
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans
from trend_memory import load_trend_memory

def extract_clustered_topics(n_clusters=5):
    history = load_trend_memory()
    all_trends = [t for entry in history for t in entry["trends"]]

    vectorizer = TfidfVectorizer(stop_words='english')
    X = vectorizer.fit_transform(all_trends)
    model = KMeans(n_clusters=n_clusters, random_state=0).fit(X)

    labels = model.labels_
    clustered = [[] for _ in range(n_clusters)]
    for i, label in enumerate(labels):
        clustered[label].append(all_trends[i])

    summarized_clusters = [max(c, key=lambda x: len(x)) for c in clustered if c]
    return summarized_clusters
