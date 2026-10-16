'use client';

import React from 'react';
import { CpuChipIcon } from '@heroicons/react/24/outline';

interface TypingIndicatorProps {
    brandColor?: string;
    message?: string;
    isVisible?: boolean;
}

const TypingIndicator: React.FC<TypingIndicatorProps> = ({
    brandColor = '#385854',
    message = 'AI is thinking...',
    isVisible = true
}) => {
    if (!isVisible) return null;

    return (
        <div className="flex items-start gap-2">
            <div
                className="w-7 h-7 rounded-full flex items-center justify-center text-white flex-shrink-0 shadow"
                style={{ backgroundColor: brandColor }}
            >
                <CpuChipIcon className="w-3.5 h-3.5" />
            </div>
            <div className="bg-white text-gray-800 max-w-[80%] px-4 py-3 rounded-2xl rounded-bl-md shadow-lg border border-gray-200">
                <div className="flex items-center gap-2">
                    <div className="flex gap-1">
                        <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                        <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                        <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                    </div>
                    <span className="text-xs text-gray-500">{message}</span>
                </div>
            </div>
        </div>
    );
};

export default TypingIndicator;
