import { useState, useRef, useEffect } from 'react';
import { Lock, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';

interface PinDialogProps {
  onSubmit: (pin: string) => Promise<boolean>;
  error?: string;
}

export default function PinDialog({ onSubmit, error }: PinDialogProps) {
  const [pin, setPin] = useState(['', '', '', '']);
  const [loading, setLoading] = useState(false);
  const inputRefs = useRef<(HTMLInputElement | null)[]>([]);

  useEffect(() => {
    inputRefs.current[0]?.focus();
  }, []);

  const handleChange = (index: number, value: string) => {
    if (!/^\d*$/.test(value)) return;
    
    const newPin = [...pin];
    newPin[index] = value.slice(-1);
    setPin(newPin);

    if (value && index < 3) {
      inputRefs.current[index + 1]?.focus();
    }
  };

  const handleKeyDown = (index: number, e: React.KeyboardEvent) => {
    if (e.key === 'Backspace' && !pin[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
    if (e.key === 'Enter' && pin.every(d => d !== '')) {
      handleSubmit();
    }
  };

  const handlePaste = (e: React.ClipboardEvent) => {
    e.preventDefault();
    const pasted = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 4);
    if (pasted.length === 4) {
      setPin(pasted.split(''));
      inputRefs.current[3]?.focus();
    }
  };

  const handleSubmit = async () => {
    const pinString = pin.join('');
    if (pinString.length !== 4) return;
    
    setLoading(true);
    const success = await onSubmit(pinString);
    setLoading(false);
    
    if (!success) {
      setPin(['', '', '', '']);
      inputRefs.current[0]?.focus();
    }
  };

  return (
    <div className="min-h-screen bg-background text-foreground font-mono flex items-center justify-center p-4">
      <div className="w-full max-w-sm space-y-6">
        <div className="text-center space-y-2">
          <div className="flex justify-center">
            <div className="p-3 border border-border">
              <Lock className="w-8 h-8 text-primary" />
            </div>
          </div>
          <h1 className="text-lg font-bold tracking-tight text-primary">ACCESS REQUIRED</h1>
          <p className="text-sm text-muted-foreground">Enter 4-digit PIN to continue</p>
        </div>

        <div 
          className={cn(
            "flex justify-center gap-3",
            error && "animate-shake"
          )}
          onPaste={handlePaste}
        >
          {pin.map((digit, index) => (
            <input
              key={index}
              ref={el => { inputRefs.current[index] = el; }}
              type="password"
              inputMode="numeric"
              maxLength={1}
              value={digit}
              onChange={e => handleChange(index, e.target.value)}
              onKeyDown={e => handleKeyDown(index, e)}
              className={cn(
                "w-12 h-14 text-center text-2xl font-bold",
                "border bg-background",
                "focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary",
                "transition-all duration-200",
                error ? "border-destructive" : "border-border"
              )}
              disabled={loading}
            />
          ))}
        </div>

        {error && (
          <div className="flex items-center justify-center gap-2 text-destructive text-sm">
            <AlertCircle className="w-4 h-4" />
            <span>{error}</span>
          </div>
        )}

        <Button
          onClick={handleSubmit}
          disabled={loading || pin.some(d => d === '')}
          className="w-full"
        >
          {loading ? 'VERIFYING...' : 'UNLOCK'}
        </Button>

        <p className="text-xs text-center text-muted-foreground">
          PIN was set when starting the server
        </p>
      </div>

      <style>{`
        @keyframes shake {
          0%, 100% { transform: translateX(0); }
          20%, 60% { transform: translateX(-8px); }
          40%, 80% { transform: translateX(8px); }
        }
        .animate-shake {
          animation: shake 0.5s ease-in-out;
        }
      `}</style>
    </div>
  );
}
