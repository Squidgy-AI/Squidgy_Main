// src/components/Chat/MessageContent.tsx
'use client';

import React from 'react';

interface MessageContentProps {
  text: string;
  isUser: boolean;
}

const MessageContent: React.FC<MessageContentProps> = ({ text, isUser }) => {
  // Function to detect if a URL is an image
  const isImageUrl = (url: string): boolean => {
    const imageExtensions = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp'];
    return imageExtensions.some(ext => url.toLowerCase().includes(ext));
  };

  // Function to detect if a URL is a video
  const isVideoUrl = (url: string): boolean => {
    const videoExtensions = ['.mp4', '.webm', '.ogg', '.mov', '.avi', '.mkv'];
    return videoExtensions.some(ext => url.toLowerCase().includes(ext));
  };

  // Function to extract URLs from text
  const extractUrls = (text: string) => {
    const urlRegex = /(https?:\/\/[^\s]+)/g;
    return text.match(urlRegex) || [];
  };

  const urls = extractUrls(text);
  const textParts = text.split(/(https?:\/\/[^\s]+)/g);

  return (
    <div className="space-y-2">
      {/* Text content with proper word wrapping */}
      <div className="break-words whitespace-pre-wrap">
        {textParts.map((part, index) => {
          if (part.match(/https?:\/\/[^\s]+/)) {
            return (
              <a
                key={index}
                href={part}
                target="_blank"
                rel="noopener noreferrer"
                className={`underline hover:no-underline ${
                  isUser ? 'text-blue-200' : 'text-green-200'
                }`}
              >
                {part}
              </a>
            );
          }
          return <span key={index}>{part}</span>;
        })}
      </div>

      {/* Media content */}
      {urls.map((url, index) => {
        if (isImageUrl(url)) {
          return (
            <div key={`img-${index}`} className="mt-2">
              <img
                src={url}
                alt="Shared image"
                className="max-w-full h-auto rounded-lg border border-gray-600 shadow-lg"
                style={{ maxHeight: '300px', maxWidth: '100%' }}
                onError={(e) => {
                  const target = e.target as HTMLImageElement;
                  target.style.display = 'none';
                }}
              />
            </div>
          );
        }

        if (isVideoUrl(url)) {
          return (
            <div key={`video-${index}`} className="mt-2">
              <video
                controls
                className="max-w-full h-auto rounded-lg border border-gray-600 shadow-lg"
                style={{ maxHeight: '300px', maxWidth: '100%' }}
              >
                <source src={url} type="video/mp4" />
                <source src={url} type="video/webm" />
                <source src={url} type="video/ogg" />
                Your browser does not support the video tag.
              </video>
            </div>
          );
        }

        return null;
      })}
    </div>
  );
};

export default MessageContent;