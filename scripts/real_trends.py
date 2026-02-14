#!/usr/bin/env python3
import requests
from bs4 import BeautifulSoup
import json
import random

def fetch_real_trends():
    """Fetch real trends from various sources."""
    trends = []
    
    # Try multiple sources
    sources = [
        fetch_twitter_trends,
        fetch_google_trends,
        fetch_reddit_trends,
        fetch_youtube_trends
    ]
    
    for source in sources:
        try:
            trends.extend(source())
        except Exception as e:
            print(f"{source.__name__} failed: {e}")
    
    # Remove duplicates and ensure we have trends
    trends = list(set(trends))
    
    # Fallback to mock trends if no real trends found
    if not trends:
        trends = [
            "Dancing Videos", "TikTok Duets", "ASMR", 
            "AI Art Challenges", "Viral Animal Videos",
            "Cooking Tutorials", "Fitness Challenges", "Prank Videos",
            "Gaming Highlights", "Life Hacks", "Travel Vlogs"
        ]
    
    return trends

def fetch_twitter_trends():
    """Fetch trending topics from Twitter."""
    try:
        response = requests.get(
            "https://trends24.in/",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10
        )
        
        soup = BeautifulSoup(response.text, 'html.parser')
        trends = []
        
        for item in soup.select('.trend-card__list li a'):
            trend = item.text.strip()
            if trend and trend not in trends:
                trends.append(trend)
        
        return trends[:5]  # Return top 5
    except:
        return []

def fetch_google_trends():
    """Fetch trending topics from Google Trends."""
    try:
        response = requests.get(
            "https://trends.google.com/trends/api/dailytrends?hl=en-US&geo=US",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10
        )
        
        # Google Trends returns JSON with a prefix
        data = json.loads(response.text[5:])
        trends = []
        
        for topic in data['default']['trendingSearchesDays'][0]['trendingSearches']:
            trends.append(topic['title']['query'])
        
        return trends[:5]  # Return top 5
    except:
        return []

def fetch_reddit_trends():
    """Fetch trending topics from Reddit."""
    try:
        response = requests.get(
            "https://www.reddit.com/r/popular.json",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10
        )
        
        data = response.json()
        trends = []
        
        for post in data['data']['children']:
            title = post['data']['title']
            trends.append(title)
        
        return trends[:5]  # Return top 5
    except:
        return []

def fetch_youtube_trends():
    """Fetch trending topics from YouTube."""
    try:
        response = requests.get(
            "https://www.youtube.com/feed/trending",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10
        )
        
        soup = BeautifulSoup(response.text, 'html.parser')
        trends = []
        
        for item in soup.select('h3 a'):
            title = item.get('title')
            if title and title not in trends:
                trends.append(title)
        
        return trends[:5]  # Return top 5
    except:
        return []

if __name__ == "__main__":
    trends = fetch_real_trends()
    print("Current trends:", trends)
